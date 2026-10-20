"""XBRL coverage probe: which quarterly filings can be downloaded from the CMF, per issuer.

For every issuer and quarter it asks the reused `eqr` connector for the XBRL package,
parses the instance and records whether consolidated statement facts for that quarter
are present. Packages go to a temporary directory that is deleted at the end; only the
coverage table (and, optionally, a CSV) is produced.

    python -m clinics.probe              # every quarter from 2016Q1 to the last closed quarter
    python -m clinics.probe --quick      # a handful of sample quarters per issuer
    python -m clinics.probe --issuers clc indisa --start 2023Q1 --csv coverage.csv
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .issuers import Issuer, load_issuers

# Facts that must be there for a quarter to count as usable for the benchmark.
KEY_CONCEPTS = {
    "revenue": ("ifrs-full:Revenue", "ifrs-full:RevenueFromContractsWithCustomers"),
    "assets": ("ifrs-full:Assets",),
    "trade_receivables": (
        "ifrs-full:TradeAndOtherCurrentReceivables",
        "ifrs-full:CurrentTradeReceivables",
    ),
}
QUICK_PERIODS = ["2016Q4", "2019Q4", "2022Q4", "2024Q2", "2025Q4", "2026Q2"]


@dataclass(frozen=True, order=True)
class Quarter:
    year: int
    q: int

    @property
    def month(self) -> int:
        return 3 * self.q

    @property
    def end(self) -> dt.date:
        return (pd.Timestamp(self.year, self.month, 1) + pd.offsets.MonthEnd(0)).date()

    def __str__(self) -> str:
        return f"{self.year}Q{self.q}"

    @classmethod
    def parse(cls, text: str) -> Quarter:
        year, q = text.upper().split("Q")
        if int(q) not in (1, 2, 3, 4):
            raise ValueError(f"bad quarter {text!r}")
        return cls(int(year), int(q))


def last_closed_quarter(today: dt.date) -> Quarter:
    q = (today.month - 1) // 3  # quarter that ended before the current one
    return Quarter(today.year, q) if q else Quarter(today.year - 1, 4)


def quarter_range(start: Quarter, end: Quarter) -> list[Quarter]:
    out, y, q = [], start.year, start.q
    while (y, q) <= (end.year, end.q):
        out.append(Quarter(y, q))
        y, q = (y, q + 1) if q < 4 else (y + 1, 1)
    return out


def check_quarter(filings, issuer: Issuer, quarter: Quarter) -> dict:
    """Download, parse and discard one package. Never raises: the outcome is a row."""
    from eqr.data import xbrl

    row = {"issuer": issuer.key, "rut": issuer.rut_label, "period": str(quarter), "status": "missing"}
    try:
        data = filings.package(issuer.rut, quarter.year, quarter.month)
    except (LookupError, FileNotFoundError):
        return row
    except Exception as err:  # network or HTTP error: report, keep going
        return {**row, "status": f"error: {type(err).__name__}"}
    finally:
        cached = filings._cached(issuer.rut, quarter.year, quarter.month)
        size = cached.stat().st_size if cached.exists() else 0
        cached.unlink(missing_ok=True)
    try:
        facts = xbrl.parse_instance(xbrl.instance_from_zip(data))
        values = xbrl.period_values(facts, quarter.end)
    except Exception as err:
        return {**row, "status": f"unreadable: {type(err).__name__}", "kb": round(size / 1024)}
    found = {name: any(t in values.index for t in tags) for name, tags in KEY_CONCEPTS.items()}
    return {
        **row,
        "status": "xbrl" if all(found.values()) else "xbrl-partial",
        "kb": round(size / 1024),
        "facts": len(facts),
        "dimensional_facts": int((facts["dimensions"] > 0).sum()),
        **{f"has_{k}": v for k, v in found.items()},
    }


def run(issuers: list[Issuer], quarters: list[Quarter], pause: float, workdir: Path) -> pd.DataFrame:
    from eqr.data.cmf import CmfFilings

    rows = []
    for issuer in issuers:
        filings = CmfFilings(workdir / issuer.key)
        filings.entity_params = {**filings.entity_params, "vig": issuer.cmf_status}
        for quarter in quarters:
            row = check_quarter(filings, issuer, quarter)
            rows.append(row)
            print(f"  {issuer.short:<11} {quarter}  {row['status']}", file=sys.stderr, flush=True)
            time.sleep(pause)
    return pd.DataFrame(rows)


def summary(results: pd.DataFrame, issuers: dict[str, Issuer]) -> str:
    """Markdown: one line per issuer with first/last period, count and gaps."""
    lines = [
        "| Issuer | RUT | Type | Usable quarters | First | Last | Gaps / partial |",
        "|---|---|---|---|---|---|---|",
    ]
    for key, grp in results.groupby("issuer", sort=False):
        iss = issuers[key]
        ok = grp[grp["status"] == "xbrl"]["period"].tolist()
        first, last = (ok[0], ok[-1]) if ok else ("—", "—")
        inside = grp[(grp["period"] >= first) & (grp["period"] <= last)] if ok else grp.iloc[0:0]
        issues = [f"{p} ({s})" for p, s in zip(inside["period"], inside["status"], strict=True) if s != "xbrl"]
        errors = [f"{p} ({s})" for p, s in zip(grp["period"], grp["status"], strict=True) if s.startswith("error")]
        flagged = ", ".join(dict.fromkeys(issues + errors)) or "none"
        lines.append(
            f"| {iss.short} | {iss.rut_label} | {iss.type} | {len(ok)}/{len(grp)} | {first} | {last} | {flagged} |"
        )
    return "\n".join(lines)


def matrix(results: pd.DataFrame) -> str:
    """Markdown grid issuer x year, one symbol per quarter: ● usable, ◐ partial, · missing, ! error."""
    sym = {"xbrl": "●", "xbrl-partial": "◐", "missing": "·"}
    r = results.assign(
        year=results["period"].str[:4],
        mark=results["status"].map(lambda s: sym.get(s, "!")),
    )
    grid = r.pivot_table(index="issuer", columns="year", values="mark", aggfunc="".join, sort=False)
    head = "| Issuer | " + " | ".join(grid.columns) + " |"
    sep = "|---|" + "---|" * len(grid.columns)
    body = [f"| {i} | " + " | ".join(v if isinstance(v, str) else "" for v in row) + " |" for i, row in grid.iterrows()]
    return "\n".join([head, sep, *body])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--issuers", nargs="*", help="issuer keys from config/issuers.yaml (default: all)")
    p.add_argument("--start", default="2016Q1")
    p.add_argument("--end", help="default: last closed quarter")
    p.add_argument("--quick", action="store_true", help=f"only {', '.join(QUICK_PERIODS)}")
    p.add_argument("--pause", type=float, default=0.5, help="seconds between requests (be polite)")
    p.add_argument("--csv", type=Path, help="also write the long table here")
    args = p.parse_args(argv)

    catalog = load_issuers()
    keys = args.issuers or list(catalog)
    unknown = set(keys) - set(catalog)
    if unknown:
        p.error(f"unknown issuer(s): {', '.join(sorted(unknown))}")
    end = Quarter.parse(args.end) if args.end else last_closed_quarter(dt.date.today())
    if args.quick:
        quarters = [Quarter.parse(x) for x in QUICK_PERIODS if Quarter.parse(x) <= end]
    else:
        quarters = quarter_range(Quarter.parse(args.start), end)

    workdir = Path(tempfile.mkdtemp(prefix="clinics-probe-"))
    try:
        print(f"Probing {len(keys)} issuers x {len(quarters)} quarters (downloads in {workdir})", file=sys.stderr)
        results = run([catalog[k] for k in keys], quarters, args.pause, workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    if args.csv:
        results.to_csv(args.csv, index=False)
    print("## XBRL coverage\n")
    print(summary(results, catalog))
    print("\n● usable · missing ◐ statements incomplete ! download error (one symbol per quarter)\n")
    print(matrix(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
