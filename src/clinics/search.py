"""Search the filings for concepts matching a pattern (development aid).

For each issuer, lists the matching concepts reported for the filing's own period (balance at
the quarter end, or a period ending then), with their dimension members, how many filings carry
them, and their value in chosen periods. Nothing is written to the repository.

    python -m clinics.search --pattern "(?i)deterior|impair" --values 2023Q4 2025Q4
    python -m clinics.search --pattern "(?i)revalu" --issuers indisa --periods 2025Q3 2025Q4 --annotate
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import shutil
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from .facts import normalize_concept, parse
from .fetch import Quarter, iter_instances, last_closed_quarter, quarter_range
from .issuers import load_issuers

CHUNK = 3000  # characters per annotation (longer messages are truncated or dropped by GitHub)
MAX_NOTICES = 9  # GitHub keeps 10 notices per step


def _short(name: str) -> str:
    name = normalize_concept(name).split(":")[-1]
    return name.replace("Member", "").replace("Axis", "").replace("Miembro", "").replace("Eje", "")


def _members(members) -> str:
    return ",".join(f"{_short(a)}={_short(m)}" for a, m in members)


def _kind(start, end, quarter: Quarter) -> str:
    if start is None:
        return "instant"
    if start == dt.date(quarter.year, 1, 1):
        return "ytd"
    months = (end.year - start.year) * 12 + end.month - start.month + 1
    return f"{months}m"


def run(issuers, quarters, pattern: re.Pattern, values: set[str], pause: float, exclude=None, max_dims: int = 1):
    found = defaultdict(lambda: {"filings": set(), "values": {}})
    workdir = Path(tempfile.mkdtemp(prefix="clinics-search-"))
    try:
        for issuer, quarter, _status, instance in iter_instances(issuers, quarters, workdir, pause):
            if instance is None:
                continue
            f = parse(instance)
            f = f[f["end"] == quarter.end]
            for r in f.itertuples(index=False):
                concept = normalize_concept(r.concept)
                if not pattern.search(concept) or (exclude and exclude.search(concept)) or r.dimensions > max_dims:
                    continue
                start = None if r.instant else r.start
                key = (issuer.key, concept, _members(r.members), _kind(start, r.end, quarter))
                entry = found[key]
                entry["filings"].add(str(quarter))
                if str(quarter) in values and str(quarter) not in entry["values"]:
                    entry["values"][str(quarter)] = r.value
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return found


def lines(found, issuer: str, values: list[str]) -> list[str]:
    rows = [(k, v) for k, v in found.items() if k[0] == issuer]
    rows.sort(key=lambda kv: (-len(kv[1]["filings"]), kv[0][1], kv[0][2]))
    out = []
    for (_, concept, members, kind), v in rows:
        f = sorted(v["filings"])
        vals = " ".join(f"{p}={v['values'][p] / 1e9:,.2f}bn" for p in values if p in v["values"])
        dims = f" [{members}]" if members else ""
        out.append(f"{concept.split(':', 1)[-1]}{dims} {kind} n={len(f)} {f[0]}..{f[-1]} {vals}".rstrip())
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pattern", required=True, help="regular expression on the concept name (prefix included)")
    p.add_argument("--issuers", nargs="*")
    p.add_argument("--periods", nargs="*", help="only these quarters (default: all since --start)")
    p.add_argument("--start", default="2016Q1")
    p.add_argument("--values", nargs="*", default=[], help="quarters whose values are shown")
    p.add_argument("--exclude", help="regular expression of concepts to drop")
    p.add_argument("--max-dims", type=int, default=1, help="drop facts with more dimensions than this")
    p.add_argument("--pause", type=float, default=0.5)
    p.add_argument("--annotate", action="store_true", help="also emit one GitHub Actions notice per issuer")
    args = p.parse_args(argv)

    catalog = load_issuers()
    issuers = [catalog[k] for k in (args.issuers or catalog)]
    if args.periods:
        quarters = sorted(Quarter.parse(x) for x in args.periods)
    else:
        quarters = quarter_range(Quarter.parse(args.start), last_closed_quarter(dt.date.today()))
    values = list(args.values or (args.periods or []))
    exclude = re.compile(args.exclude) if args.exclude else None
    found = run(issuers, quarters, re.compile(args.pattern), set(values), args.pause, exclude, args.max_dims)
    notices = []
    for issuer in issuers:
        text = lines(found, issuer.key, values)
        print(f"## {issuer.short}: {len(text)} matches")
        print("\n".join(text) or "(none)")
        if args.annotate:
            chunks, cur = [], ""
            for line in text or ["(none)"]:
                if len(cur) + len(line) + 3 > CHUNK:
                    chunks.append(cur)
                    cur = ""
                cur += ("%0A" if cur else "") + line
            chunks.append(cur)
            notices.extend((issuer.short, i + 1, len(chunks), c) for i, c in enumerate(chunks))
    if args.annotate:
        for short, i, n, body in notices[:MAX_NOTICES]:
            print(f"::notice title=Search {short} {i}/{n}::{body}", file=sys.stderr)
        if len(notices) > MAX_NOTICES:
            print(
                f"::warning title=Search truncated::{len(notices) - MAX_NOTICES} more chunks; narrow the pattern",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
