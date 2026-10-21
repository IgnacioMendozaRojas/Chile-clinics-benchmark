"""Rebuild every published table from the CMF filings.

    python -m clinics.update --out data/processed

Downloads every issuer's quarterly XBRL filing (deleting each package after parsing),
builds the canonical statements and indicators, runs the validations and writes:

    statements.csv        issuer x quarter x variable (CLP; balances, YTD, quarter, LTM)
    notes.csv             issuer x quarter x note item (receivables ageing, allowance, payables)
    indicators.csv        issuer x quarter x indicator
    coverage.csv          which quarters each issuer filed
    restatements.csv      figures that a later filing presents differently (as reported vs revised)
    validation.csv / validation_report.md

Exits with status 1 when any validation error is found, so the workflow does not commit.
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sys
import tempfile
from pathlib import Path

import pandas as pd

from . import indicators, notes, statements, validate
from .facts import as_reported, consolidated_facts, parse
from .fetch import Quarter, iter_instances, last_closed_quarter, quarter_range
from .issuers import load_issuers


def collect(issuers, quarters, pause: float) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(consolidated facts from every filing, note facts, coverage rows)."""
    frames, note_frames, coverage = [], [], []
    note_cfg = notes.load_notes()
    workdir = Path(tempfile.mkdtemp(prefix="clinics-update-"))
    try:
        for issuer, quarter, status, instance in iter_instances(issuers, quarters, workdir, pause):
            coverage.append({"issuer": issuer.key, "period": str(quarter), "status": status})
            if instance is not None:
                parsed = parse(instance)
                frames.append(consolidated_facts(parsed, issuer.key, quarter))
                note_frames.append(notes.note_facts(parsed, issuer.key, quarter, note_cfg))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    facts = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    note_facts = pd.concat(note_frames, ignore_index=True) if note_frames else notes.note_facts_empty()
    return facts, note_facts, pd.DataFrame(coverage)


def build_all(
    facts: pd.DataFrame, note_facts: pd.DataFrame, coverage: pd.DataFrame, catalog
) -> dict[str, pd.DataFrame]:
    variables = statements.load_variables()
    reported = as_reported(facts)
    filed = coverage[coverage["status"] == "ok"]
    periods = {k: [Quarter.parse(p) for p in g["period"]] for k, g in filed.groupby("issuer", sort=False)}
    st = statements.build(reported, periods, variables)
    note_table = notes.build(note_facts, notes.load_notes())
    ind = pd.concat([indicators.compute(st), notes.compute_indicators(note_table)], ignore_index=True)
    meta = pd.DataFrame([{"issuer": k, "issuer_type": i.type, "panel": i.panel} for k, i in catalog.items()])
    ind = ind.merge(meta, on="issuer", how="left")
    rest = statements.restatements(reported, variables)
    findings = pd.concat([validate.run(st, rest), notes.checks(note_table)], ignore_index=True)
    findings = findings.sort_values(["severity", "issuer", "period", "check"]).reset_index(drop=True)
    cov = filed.groupby("issuer", sort=False)["period"].agg(quarters="count", first="min", last="max").reset_index()
    return {
        "statements": st,
        "notes": note_table,
        "indicators": ind,
        "coverage": coverage,
        "coverage_summary": cov,
        "restatements": rest,
        "validation": findings,
    }


def write(tables: dict[str, pd.DataFrame], out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    tables["statements"].to_csv(out / "statements.csv", index=False, float_format="%.0f")
    tables["notes"].to_csv(out / "notes.csv", index=False, float_format="%.0f")
    tables["indicators"].to_csv(out / "indicators.csv", index=False, float_format="%.6g")
    tables["coverage"].to_csv(out / "coverage.csv", index=False)
    tables["restatements"].to_csv(out / "restatements.csv", index=False, float_format="%.6g")
    tables["validation"].to_csv(out / "validation.csv", index=False, float_format="%.6g")
    (out / "validation_report.md").write_text(
        validate.report(tables["validation"], tables["coverage_summary"]), encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, default=Path("data/processed"))
    p.add_argument("--issuers", nargs="*")
    p.add_argument("--start", default="2016Q1")
    p.add_argument("--pause", type=float, default=0.5)
    p.add_argument("--annotate", action="store_true", help="emit GitHub Actions annotations for errors")
    args = p.parse_args(argv)

    catalog = load_issuers()
    issuers = [catalog[k] for k in (args.issuers or catalog)]
    quarters = quarter_range(Quarter.parse(args.start), last_closed_quarter(dt.date.today()))
    facts, note_facts, coverage = collect(issuers, quarters, args.pause)
    if facts.empty:
        print("no filings could be read", file=sys.stderr)
        return 1
    tables = build_all(facts, note_facts, coverage, catalog)
    write(tables, args.out)

    v = tables["validation"]
    counts = v["severity"].value_counts()
    summary = f"{counts.get('error', 0)} errors, {counts.get('warning', 0)} warnings, {counts.get('info', 0)} info"
    print(summary)
    if args.annotate:
        print(f"::notice title=Validation::{summary}", file=sys.stderr)
        for r in v[v["severity"] == "error"].head(10).itertuples(index=False):
            print(f"::error title={r.check} {r.issuer} {r.period}::{r.message}", file=sys.stderr)
    return 1 if counts.get("error", 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
