"""Notes from XBRL dimensions: receivables ageing, allowance for credit losses, overdue payables.

Facts come from the period's own filing (balance at the quarter end, whether tagged as an instant
or as a year-to-date period ending then), on one axis only (facts that combine the axis with
another breakdown are skipped). See `config/notes.yaml`.

Indicators (all shares of the same note's total, so they compare across issuers regardless
of how each defines the receivables portfolio):

* receivables_overdue_share   gross receivables not current / total gross portfolio
* receivables_over_180_share  more than 180 days past due / total
* allowance_coverage          allowance for credit losses / receivables before allowance
* payables_overdue_share      overdue trade payables / (not yet due + overdue)
* payables_over_90_share      overdue more than 90 days / (not yet due + overdue), published only
                              when the overdue buckets add up to the overdue total (complete breakdown)
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from .facts import normalize_concept
from .fetch import Quarter

CONFIG = Path(__file__).resolve().parents[2] / "config" / "notes.yaml"
if not CONFIG.exists():  # installed wheel
    CONFIG = Path(__file__).resolve().parent / "_config" / "notes.yaml"

BUCKET_TOLERANCE = 0.01  # buckets must add up to the total within 1%
OVER_180 = ("d181_210", "d211_250", "d250_plus")
PAYABLES_OVER_90 = ("d091_120", "d121_180", "d180_plus")
PAYABLES_BUCKETS = ("d001_030", "d031_060", "d061_090", *PAYABLES_OVER_90)

INDICATORS = {
    "receivables_overdue_share": ("Receivables", "Overdue share of gross trade receivables", "%"),
    "receivables_over_180_share": ("Receivables", "Share of gross trade receivables > 180 days past due", "%"),
    "allowance_coverage": ("Receivables", "Allowance for credit losses / gross receivables", "%"),
    "payables_overdue_share": ("Payables", "Overdue share of trade payables", "%"),
    "payables_over_90_share": ("Payables", "Share of trade payables > 90 days overdue", "%"),
}


@dataclass(frozen=True)
class Note:
    name: str
    axis: str
    by: str  # member | concept
    items: dict = field(default_factory=dict)
    concept: str | None = None
    label: str = ""


def load_notes(path: Path = CONFIG) -> dict[str, Note]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))["notes"]
    return {
        name: Note(
            name=name,
            axis=spec["axis"],
            by=spec["by"],
            items=dict(spec["items"]),
            concept=spec.get("concept"),
            label=spec.get("label", name),
        )
        for name, spec in raw.items()
    }


NOTE_FACT_COLUMNS = ["issuer", "filing", "concept", "axis", "member", "value"]


def note_facts_empty() -> pd.DataFrame:
    return pd.DataFrame(columns=NOTE_FACT_COLUMNS)


def note_facts(parsed: pd.DataFrame, issuer: str, filing: Quarter, notes: dict[str, Note]) -> pd.DataFrame:
    """Single-axis facts at the filing's quarter end on any configured note axis."""
    axes = {n.axis for n in notes.values()}
    # balances at the quarter end; some notes (e.g. the portfolio stratification) are tagged with a
    # year-to-date period ending at the quarter end instead of an instant: accept both, instants first
    own = (parsed["end"] == filing.end) & (parsed["instant"] | (parsed["start"] == dt.date(filing.year, 1, 1)))
    f = parsed[(parsed["dimensions"] == 1) & own].sort_values("instant", ascending=False, kind="stable")
    rows = []
    for r in f.itertuples(index=False):
        ((axis, member),) = r.members
        axis = normalize_concept(axis)
        if axis in axes:
            rows.append(
                {
                    "issuer": issuer,
                    "filing": str(filing),
                    "concept": normalize_concept(r.concept),
                    "axis": axis,
                    "member": normalize_concept(member),
                    "value": r.value,
                }
            )
    return pd.DataFrame(rows, columns=NOTE_FACT_COLUMNS).drop_duplicates(["concept", "axis", "member"])


def build(facts: pd.DataFrame, notes: dict[str, Note]) -> pd.DataFrame:
    """Long table: issuer, period, note, item, value (CLP; allowance as a positive amount)."""
    out = []
    for note in notes.values():
        f = facts[facts["axis"] == note.axis]
        if note.by == "member":
            f = f[f["concept"] == note.concept]
            item = f["member"].map(note.items)
        else:
            item = f["concept"].map(note.items)
        f = f.assign(item=item).dropna(subset=["item"])
        g = f.groupby(["issuer", "filing", "item"], sort=False)["value"].sum().reset_index()
        g["value"] = g["value"].abs() if note.name == "receivables_allowance" else g["value"]
        out.append(g.assign(note=note.name))
    if not out:
        return pd.DataFrame(columns=["issuer", "period", "note", "item", "value"])
    t = pd.concat(out, ignore_index=True).rename(columns={"filing": "period"})
    return t[["issuer", "period", "note", "item", "value"]].sort_values(["issuer", "period", "note", "item"])


def _get(table: pd.DataFrame, note: str, item: str) -> pd.Series:
    s = table[(table["note"] == note) & (table["item"] == item)].set_index(["issuer", "period"])["value"]
    return s[~s.index.duplicated()]


def _div(a: pd.Series, b: pd.Series) -> pd.Series:
    return a / b.where(b.abs() > 0)


def compute_indicators(table: pd.DataFrame) -> pd.DataFrame:
    """Long table: issuer, period, indicator, value."""
    if table.empty:
        return pd.DataFrame(columns=["issuer", "period", "indicator", "value"])
    ageing = table[table["note"] == "receivables_ageing"].pivot_table(
        index=["issuer", "period"], columns="item", values="value", aggfunc="sum"
    )
    total_rec = ageing.sum(axis=1, min_count=1)
    over_180 = ageing.reindex(columns=list(OVER_180)).sum(axis=1, min_count=1)
    current = ageing.get("current", pd.Series(np.nan, index=ageing.index))

    pay_cur = _get(table, "payables_current", "total")
    overdue = table[table["note"] == "payables_overdue"].pivot_table(
        index=["issuer", "period"], columns="item", values="value", aggfunc="sum"
    )
    pay_over = overdue.get("total", pd.Series(np.nan, index=overdue.index))
    buckets = overdue.reindex(columns=list(PAYABLES_BUCKETS)).fillna(0).sum(axis=1)
    complete = (buckets - pay_over).abs() <= BUCKET_TOLERANCE * pay_over.abs().clip(lower=1)
    over_90 = overdue.reindex(columns=list(PAYABLES_OVER_90)).fillna(0).sum(axis=1).where(complete)
    pay_total = pay_cur + pay_over

    parts = {
        "receivables_overdue_share": 1 - _div(current, total_rec),
        "receivables_over_180_share": _div(over_180, total_rec),
        "allowance_coverage": _div(
            _get(table, "receivables_allowance", "allowance"), _get(table, "receivables_allowance", "gross")
        ),
        "payables_overdue_share": _div(pay_over, pay_total),
        "payables_over_90_share": _div(over_90, pay_total),
    }
    frames = [s.rename("value").reset_index().assign(indicator=name) for name, s in parts.items() if len(s)]
    out = pd.concat(frames, ignore_index=True).replace([np.inf, -np.inf], np.nan).dropna(subset=["value"])
    return out[["issuer", "period", "indicator", "value"]].reset_index(drop=True)


def checks(table: pd.DataFrame) -> pd.DataFrame:
    """Findings in validate.run()'s format."""
    rows = []

    def add(severity, check, issuer, period, message, value=math.nan):
        rows.append(
            {
                "severity": severity,
                "check": check,
                "issuer": issuer,
                "period": period,
                "value": value,
                "message": message,
            }
        )

    gross = _get(table, "receivables_allowance", "gross")
    allowance = _get(table, "receivables_allowance", "allowance")
    for key in allowance.index.intersection(gross.index):
        if allowance[key] > gross[key]:
            add("warning", "allowance", *key, "allowance larger than gross receivables", allowance[key] / gross[key])
    cur = table[table["note"] == "payables_current"].pivot_table(
        index=["issuer", "period"], columns="item", values="value", aggfunc="sum"
    )
    if "total" in cur:
        parts = cur.drop(columns="total").sum(axis=1, min_count=1)
        for key, total in cur["total"].items():
            p = parts.get(key, math.nan)
            if not math.isnan(p) and abs(p - total) > BUCKET_TOLERANCE * max(abs(total), 1):
                add(
                    "warning",
                    "payables_buckets",
                    *key,
                    "not-yet-due payable buckets do not add up to the total",
                    p / total - 1 if total else math.nan,
                )
    return pd.DataFrame(rows, columns=["severity", "check", "issuer", "period", "value", "message"])
