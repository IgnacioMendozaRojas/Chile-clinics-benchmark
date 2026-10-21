"""Tables in data/processed -> latest.json and the "Latest reading" block of the README.

    python -m clinics.build --data data/processed --readme README.md

Same schema as chile-mutual-funds-analytics/data/processed/latest.json. `updated` is the date
the published reading last changed, so a run with nothing new rewrites nothing.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path

import pandas as pd

from .issuers import load_issuers

METRICS = {
    # indicator: label
    "allowance_coverage": "Allowance for credit losses / gross trade receivables",
    "receivables_over_180_share": "Gross trade receivables more than 180 days past due",
    "cost_of_risk": "Cost of risk (credit losses / revenue, LTM)",
    "ebitda_before_credit_losses_margin": "EBITDA margin before credit losses, LTM",
}
FIGURE = "figures/allowance_coverage.png"


def _prev(period: str) -> str:
    y, q = int(period[:4]), int(period[-1])
    return f"{y - 1}Q4" if q == 1 else f"{y}Q{q - 1}"


def latest(data: Path, today: dt.date | None = None) -> dict:
    ind = pd.read_csv(data / "indicators.csv")
    seq = pd.read_csv(data / "sequence.csv") if (data / "sequence.csv").exists() else pd.DataFrame()
    issuers = load_issuers()
    core = [k for k, i in issuers.items() if i.panel == "core"]
    metrics = []
    for key in core:
        for indicator, label in METRICS.items():
            s = ind[(ind["issuer"] == key) & (ind["indicator"] == indicator)].set_index("period")["value"]
            if s.empty:
                continue
            period = max(s.index)
            prev = s.get(_prev(period))
            value = float(s[period])
            metrics.append(
                {
                    "id": f"{key}_{indicator}",
                    "label": f"{issuers[key].short}: {label}",
                    "value": round(value, 4),
                    "unit": "share",
                    "period": period,
                    "previous": None if prev is None or pd.isna(prev) else round(float(prev), 4),
                    "change": None if prev is None or pd.isna(prev) else round(value - float(prev), 4),
                    "change_type": "points",
                }
            )
    as_of = max(m["period"] for m in metrics)
    return {
        "schema": 1,
        "id": "clinics-isapre-crisis",
        "repo": "chile-clinics-benchmark",
        "title": "Chilean private clinics through the Isapre crisis",
        "question": (
            "How hard did Chile's Isapre crisis hit the private clinic operators that report to the CMF, "
            "and did it show up first in receivables, impairments or liquidity?"
        ),
        "as_of": as_of,
        "frequency": "quarterly",
        "updated": (today or dt.date.today()).isoformat(),
        "source": "CMF XBRL filings: financial statements and notes (as reported)",
        "headline": headline(metrics, seq, issuers),
        "figure": FIGURE,
        "metrics": metrics,
    }


def headline(metrics: list[dict], seq: pd.DataFrame, issuers) -> str:
    """One sentence from the data: the core issuer whose allowance coverage is furthest above its
    pre-crisis maximum, and the cost of risk across the core panel."""
    cov = [x for x in metrics if x["id"].endswith("_allowance_coverage")]
    if not cov:
        return ""

    def ceiling(key):
        if seq.empty:
            return None
        s = seq[(seq["issuer"] == key) & (seq["indicator"] == "allowance_coverage")]["baseline_high"]
        return None if s.empty or pd.isna(s.iloc[0]) else float(s.iloc[0])

    def key_of(x):
        return x["id"].removesuffix("_allowance_coverage")

    lead = max(cov, key=lambda x: x["value"] / (ceiling(key_of(x)) or x["value"]))
    k, top = key_of(lead), ceiling(key_of(lead))
    text = (
        f"In {lead['period']}, {issuers[k].short}'s allowance covered {lead['value']:.0%} "
        "of its gross trade receivables"
    )
    text += f" (at most {top:.0%} in 2017–2021)" if top else ""
    risk = [
        f"{x['value']:.1%} at {issuers[x['id'].removesuffix('_cost_of_risk')].short}"
        for x in metrics
        if x["id"].endswith("_cost_of_risk")
    ]
    if risk:
        text += "; LTM cost of risk was " + (", ".join(risk[:-1]) + " and " + risk[-1] if len(risk) > 1 else risk[0])
    return text + "."


def _fmt_change(x: dict) -> str:
    if x["change"] is None:
        return ""
    return f"{x['change'] * 100:+.1f} pp"


def readme_block(data: dict) -> str:
    lines = [
        f"**Latest reading: {data['as_of']}.** {data['headline']}",
        "",
        "| Indicator | Latest | Quarter before | Change | Note |",
        "|---|---:|---:|---:|---|",
    ]
    for x in data["metrics"]:
        prev = "" if x["previous"] is None else f"{x['previous']:.1%}"
        note = f"latest is {x['period']}" if x["period"] != data["as_of"] else ""
        lines.append(f"| {x['label']} | {x['value']:.1%} | {prev} | {_fmt_change(x)} | {note} |")
    lines += [
        "",
        f"*Updated automatically on {data['updated']} from the CMF filings. pp = percentage points. "
        "Cost of risk is estimated from the allowance roll-forward except where the issuer files the IFRS 9 "
        "line (see Method). A note gives the quarter when an issuer lags the others.*",
    ]
    return "\n".join(lines)


def write_readme(readme: Path, block: str) -> bool:
    text = readme.read_text(encoding="utf-8")
    pattern = r"(<!-- latest:start -->\n)(?:.*?\n)?(<!-- latest:end -->)"
    if not re.search(pattern, text, flags=re.S):
        raise ValueError(f"{readme}: markers <!-- latest:start --> / <!-- latest:end --> not found")
    new = re.sub(pattern, lambda m: m.group(1) + block + "\n" + m.group(2), text, count=1, flags=re.S)
    if new != text:
        readme.write_text(new, encoding="utf-8")
    return new != text


def build(data: Path, readme: Path | None = None, today: dt.date | None = None) -> dict:
    out = Path(data) / "latest.json"
    new = latest(Path(data), today)
    if out.exists():  # keep the previous date when nothing else changed
        old = json.loads(out.read_text(encoding="utf-8"))
        if {k: v for k, v in old.items() if k != "updated"} == {k: v for k, v in new.items() if k != "updated"}:
            new["updated"] = old["updated"]
    out.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if readme:
        write_readme(Path(readme), readme_block(new))
    return new


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", type=Path, default=Path("data/processed"))
    p.add_argument("--readme", type=Path, default=Path("README.md"))
    args = p.parse_args(argv)
    data = build(args.data, args.readme)
    print(f"built: latest {data['as_of']} ({len(data['metrics'])} metrics)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
