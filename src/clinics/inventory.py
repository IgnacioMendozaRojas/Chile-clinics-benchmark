"""Concept inventory: which XBRL concepts and dimensions each issuer actually files, and when.

Development aid for writing `config/concepts.yaml` and the notes layer. Writes:

    concepts.csv     issuer x concept: kind, filings that report it, first / last filing
    dimensions.csv   issuer x concept x axis x member: filings that report it
    sample_<issuer>.csv   every consolidated fact of the issuer's first and last December filing

    python -m clinics.inventory --out inventory/
"""

from __future__ import annotations

import argparse
import datetime as dt
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path

import pandas as pd

from .facts import consolidated_facts, normalize_concept, parse
from .fetch import Quarter, iter_instances, last_closed_quarter, quarter_range
from .issuers import load_issuers


def dimension_members(parsed: pd.DataFrame) -> set[tuple[str, str, str, str]]:
    """{(concept, axis, member, kind)} for facts reported with exactly one dimension (kind: instant/duration)."""
    one = parsed[parsed["dimensions"] == 1]
    return {
        (normalize_concept(c), normalize_concept(m[0][0]), normalize_concept(m[0][1]), "instant" if i else "duration")
        for c, m, i in zip(one["concept"], one["members"], one["instant"], strict=True)
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, default=Path("inventory"))
    p.add_argument("--issuers", nargs="*")
    p.add_argument("--start", default="2016Q1")
    p.add_argument("--pause", type=float, default=0.5)
    args = p.parse_args(argv)

    catalog = load_issuers()
    issuers = [catalog[k] for k in (args.issuers or catalog)]
    quarters = quarter_range(Quarter.parse(args.start), last_closed_quarter(dt.date.today()))
    args.out.mkdir(parents=True, exist_ok=True)

    concept_rows, dim_counts = [], defaultdict(set)
    decembers: dict[str, dict[str, pd.DataFrame]] = defaultdict(dict)
    workdir = Path(tempfile.mkdtemp(prefix="clinics-inventory-"))
    try:
        for issuer, quarter, _status, instance in iter_instances(issuers, quarters, workdir, args.pause):
            if instance is None:
                continue
            parsed = parse(instance)
            facts = consolidated_facts(parsed, issuer.key, quarter)
            own = facts[facts["end"] == quarter.end]
            for concept, grp in own.groupby("concept"):
                kind = "instant" if grp["instant"].any() else "duration"
                concept_rows.append({"issuer": issuer.key, "concept": concept, "kind": kind, "filing": str(quarter)})
            for concept, axis, member, kind in dimension_members(parsed):
                dim_counts[(issuer.key, concept, axis, member, kind)].add(str(quarter))
            if quarter.q == 4:
                ytd = own[own["instant"] | (own["start"] == dt.date(quarter.year, 1, 1))]
                decembers[issuer.key][str(quarter)] = ytd[["concept", "instant", "value"]]
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    c = pd.DataFrame(concept_rows)
    concepts = (
        c.groupby(["issuer", "concept", "kind"])["filing"]
        .agg(n_filings="count", first_filing="min", last_filing="max")
        .reset_index()
        .sort_values(["issuer", "concept"])
    )
    concepts.to_csv(args.out / "concepts.csv", index=False)
    dims = pd.DataFrame(
        [
            {"issuer": i, "concept": c_, "axis": a, "member": m, "kind": k, "n_filings": len(f), "last_filing": max(f)}
            for (i, c_, a, m, k), f in dim_counts.items()
        ]
    ).sort_values(["issuer", "axis", "concept", "member"])
    dims.to_csv(args.out / "dimensions.csv", index=False)
    for key, by_year in decembers.items():
        years = sorted(by_year)
        sample = pd.concat({y: by_year[y] for y in {years[0], years[-1]}}, names=["filing"]).reset_index(0)
        sample.sort_values(["filing", "concept"]).to_csv(args.out / f"sample_{key}.csv", index=False)
    print(f"{len(concepts)} issuer-concepts, {len(dims)} issuer-dimension members written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
