"""From XBRL instances to one long table of consolidated facts, keeping every filing.

Each filing reports its own period and the comparatives (prior December balance sheet,
prior-year year-to-date flows). Keeping the filing a fact came from lets us choose, for
every (concept, period), the most recent figure filed — which picks up restatements —
and log where it differs from the figure first reported.
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


def latest(facts: pd.DataFrame) -> pd.DataFrame:
    """One row per (issuer, concept, start, end): the value from the most recent filing that reports it.

    Adds `first_value` / `first_filing` (as first reported) so restatements can be logged.
    """
    f = facts.copy()
    f["_order"] = f["filing"].map(lambda s: Quarter.parse(s).year * 4 + Quarter.parse(s).q)
    # instants have no start; a "" key keeps them (pandas treats missing keys specially)
    f["_start"] = f["start"].map(lambda d: d.isoformat() if isinstance(d, dt.date) else "")
    keys = ["issuer", "concept", "_start", "end"]
    f = f.sort_values("_order", kind="stable")
    first = f.drop_duplicates(keys, keep="first").set_index(keys)[["value", "filing"]]
    last = f.drop_duplicates(keys, keep="last").set_index(keys)
    out = last.join(first.rename(columns={"value": "first_value", "filing": "first_filing"}))
    return out.reset_index().drop(columns=["_start", "_order"])
