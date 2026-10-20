"""From XBRL instances to consolidated facts, as reported for each period.

Policy: every figure is taken from the filing of its own period (the quarter-end balance
sheet and the year-to-date flows that filing presents). Later filings restate some of those
figures in their comparative columns (the prior December balance sheet, prior-year
year-to-date flows); those revisions are recorded next to the as-reported figure but not
substituted, so quarterly and LTM figures never mix accounting bases (e.g. a restated
full year minus nine months that were never restated).

Only primary-statement periods count. Filings also carry dimensionless facts at other
dates (notes, equity roll-forwards, occasional tagging errors); those are ignored.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from .fetch import Quarter

COLUMNS = ["issuer", "filing", "concept", "start", "end", "instant", "value", "decimals"]


def normalize_concept(concept: str) -> str:
    """Use one prefix per taxonomy family, whatever the filing declared ("ifrs:" vs "ifrs-full:")."""
    prefix, _, local = concept.rpartition(":")
    p = prefix.lower()
    if p.startswith("ifrs") or "xbrl.ifrs.org" in p:
        return f"ifrs-full:{local}"
    if p.startswith("cl-") or "cmfchile" in p or "svs.cl" in p:
        return f"cl-ci:{local}"
    return concept


def consolidated_facts(instance: bytes, issuer: str, filing: Quarter) -> pd.DataFrame:
    """Dimensionless (consolidated, no segment/axis), peso-denominated numeric facts of one filing."""
    from eqr.data import xbrl

    facts = xbrl.parse_instance(instance)
    f = facts[(facts["dimensions"] == 0) & (facts["unit"].str.upper() == "CLP")].copy()
    f["concept"] = f["concept"].map(normalize_concept)
    f["issuer"] = issuer
    f["filing"] = str(filing)
    f["start"] = f["start"].where(~f["instant"], None)
    f = f.drop_duplicates(["concept", "start", "end"], keep="first")
    return f[COLUMNS].reset_index(drop=True)


def _roles(facts: pd.DataFrame) -> pd.Series:
    """'own', 'comparative' or '' for each fact, relative to the filing it came from."""
    ends = {f: Quarter.parse(f).end for f in facts["filing"].unique()}
    fe = facts["filing"].map(ends)
    year = fe.map(lambda d: d.year)
    jan1 = year.map(lambda y: dt.date(y, 1, 1))
    prev_dec = year.map(lambda y: dt.date(y - 1, 12, 31))
    prev_end = fe.map(lambda d: (pd.Timestamp(d) - pd.DateOffset(years=1) + pd.offsets.MonthEnd(0)).date())
    prev_jan1 = year.map(lambda y: dt.date(y - 1, 1, 1))
    inst = facts["instant"]
    own = (inst & (facts["end"] == fe)) | (~inst & (facts["end"] == fe) & (facts["start"] == jan1))
    comp = (inst & (facts["end"] == prev_dec)) | (~inst & (facts["end"] == prev_end) & (facts["start"] == prev_jan1))
    return pd.Series(
        ["own" if o else ("comparative" if c else "") for o, c in zip(own, comp, strict=True)], index=facts.index
    )


def as_reported(facts: pd.DataFrame) -> pd.DataFrame:
    """One row per (issuer, concept, start, end) from the period's own filing.

    `revised_value` / `revised_filing`: the figure in the most recent later filing that presents
    the same period as a comparative (NaN when no later filing does).
    """
    f = facts.assign(role=_roles(facts))
    f["_start"] = f["start"].map(lambda d: d.isoformat() if isinstance(d, dt.date) else "")
    f["_order"] = f["filing"].map(lambda s: Quarter.parse(s).year * 4 + Quarter.parse(s).q)
    keys = ["issuer", "concept", "_start", "end"]
    own = f[f["role"] == "own"].drop_duplicates(keys).set_index(keys)
    comp = (
        f[f["role"] == "comparative"]
        .sort_values("_order", kind="stable")
        .drop_duplicates(keys, keep="last")
        .set_index(keys)[["value", "filing"]]
        .rename(columns={"value": "revised_value", "filing": "revised_filing"})
    )
    out = own.join(comp, how="left").reset_index()
    return out.drop(columns=["_start", "_order", "role"])
