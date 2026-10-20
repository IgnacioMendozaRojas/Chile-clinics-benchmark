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
import io
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET

import pandas as pd

from .facts import consolidated_facts, normalize_concept
from .fetch import Quarter, iter_instances, last_closed_quarter, quarter_range
from .issuers import load_issuers

XBRLI = "{http://www.xbrl.org/2003/instance}"
XBRLDI = "{http://xbrl.org/2006/xbrldi}"


def dimension_members(instance: bytes) -> set[tuple[str, str, str]]:
    """{(concept, axis, member)} for facts reported with exactly one dimension."""
    prefixes = {}
    for _event, (prefix, uri) in ET.iterparse(io.BytesIO(instance), events=("start-ns",)):
        prefixes.setdefault(uri, prefix or "")
    root = ET.fromstring(instance)
    ctx_dims = {}
    for ctx in root.findall(f"{XBRLI}context"):
        members = [m for m in ctx.iter() if m.tag in (f"{XBRLDI}explicitMember", f"{XBRLDI}typedMember")]
        if len(members) == 1:
            m = members[0]
            value = (m.text or "").strip() or "".join(x.text or "" for x in m).strip()
            ctx_dims[ctx.get("id")] = (m.get("dimension") or "", value or "(typed)")
    out = set()
    for el in root:
        dims = ctx_dims.get(el.get("contextRef"))
        if dims is None or el.get("unitRef") is None:
            continue
        ns, local = el.tag[1:].split("}")
        concept = normalize_concept(f"{prefixes.get(ns, ns)}:{local}")
        out.add((concept, normalize_concept(dims[0]), normalize_concept(dims[1])))
    return out


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
            facts = consolidated_facts(instance, issuer.key, quarter)
            own = facts[facts["end"] == quarter.end]
            for concept, grp in own.groupby("concept"):
                kind = "instant" if grp["instant"].any() else "duration"
                concept_rows.append({"issuer": issuer.key, "concept": concept, "kind": kind, "filing": str(quarter)})
            for concept, axis, member in dimension_members(instance):
                dim_counts[(issuer.key, concept, axis, member)].add(str(quarter))
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
            {"issuer": i, "concept": c_, "axis": a, "member": m, "n_filings": len(f), "last_filing": max(f)}
            for (i, c_, a, m), f in dim_counts.items()
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
