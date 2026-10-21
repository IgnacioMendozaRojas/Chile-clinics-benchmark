"""What moved first: the quarter each indicator leaves its pre-crisis range, per issuer.

Pre-crisis range: the minimum and maximum of the indicator over 2017Q1-2021Q4 (COVID
included, so the range is wide and the test conservative). A break is the first quarter from
2022Q1 on in which the indicator is outside that range in the adverse direction for two
consecutive quarters; the first of the two is reported. Indicators with fewer than eight
quarters in the baseline are reported but marked.
"""

from __future__ import annotations

import pandas as pd

from .fetch import Quarter

BASELINE = ("2017Q1", "2021Q4")
SEARCH_FROM = "2022Q1"
PERSIST = 2  # consecutive quarters outside the range
MIN_BASELINE = 8

# indicator: (label, adverse direction: +1 = up is worse, -1 = down is worse)
INDICATORS = {
    "receivables_over_180_share": ("Receivables > 180 days", +1),
    "allowance_coverage": ("Allowance coverage", +1),
    "cost_of_risk": ("Cost of risk", +1),
    "dso": ("Days sales outstanding", +1),
    "current_ratio": ("Current ratio", -1),
    "ebitda_before_credit_losses_margin": ("EBITDA margin before credit losses", -1),
    "ebitda_margin": ("EBITDA margin", -1),
}
COLUMNS = [
    "issuer",
    "indicator",
    "label",
    "direction",
    "baseline_quarters",
    "baseline_low",
    "baseline_high",
    "first_break",
    "value_at_break",
    "quarters_from_ruling",
    "note",
]
RULING = "2022Q4"  # Supreme Court Isapre ruling


def _gap(a: str, b: str) -> int:
    qa, qb = Quarter.parse(a), Quarter.parse(b)
    return (qb.year - qa.year) * 4 + (qb.q - qa.q)


def breaks(indicators: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for (issuer, ind), g in indicators[indicators["indicator"].isin(INDICATORS)].groupby(["issuer", "indicator"]):
        label, direction = INDICATORS[ind]
        g = g.sort_values("period")
        base = g[(g["period"] >= BASELINE[0]) & (g["period"] <= BASELINE[1])]["value"]
        after = g[g["period"] >= SEARCH_FROM][["period", "value"]].reset_index(drop=True)
        row = {
            "issuer": issuer,
            "indicator": ind,
            "label": label,
            "direction": "up" if direction > 0 else "down",
            "baseline_quarters": len(base),
            "note": "",
        }
        if base.empty:
            rows.append({**row, "note": "no pre-crisis data"})
            continue
        low, high = base.min(), base.max()
        row.update(baseline_low=low, baseline_high=high)
        if len(base) < MIN_BASELINE:
            row["note"] = f"short baseline ({len(base)} quarters)"
        if len(after) < PERSIST:
            rows.append({**row, "note": "; ".join(x for x in (row["note"], "too few quarters after 2021") if x)})
            continue
        outside = (after["value"] > high) if direction > 0 else (after["value"] < low)
        hit = None
        for i in range(len(after) - PERSIST + 1):
            window = after["period"].iloc[i : i + PERSIST]
            if outside.iloc[i : i + PERSIST].all() and _gap(window.iloc[0], window.iloc[-1]) == PERSIST - 1:
                hit = i
                break
        if hit is None:
            rows.append({**row, "note": "; ".join(x for x in (row["note"], "no break") if x)})
            continue
        period = after["period"][hit]
        rows.append(
            {
                **row,
                "first_break": period,
                "value_at_break": after["value"][hit],
                "quarters_from_ruling": _gap(RULING, period),
            }
        )
    return pd.DataFrame(rows).reindex(columns=COLUMNS)


def order_table(seq: pd.DataFrame, issuers) -> str:
    """Markdown: one row per issuer, indicators in the order they broke."""
    lines = [
        "| Issuer | Order in which indicators left their 2017–2021 range (first quarter) | No break |",
        "|---|---|---|",
    ]
    for key, iss in issuers.items():
        s = seq[seq["issuer"] == key]
        if s.empty:
            continue
        hits = s.dropna(subset=["first_break"]).sort_values(["first_break", "indicator"])
        order = (
            " → ".join(f"{r.label} ({r.first_break}{'*' if 'short' in str(r.note) else ''})" for r in hits.itertuples())
            or "—"
        )
        none = ", ".join(s[s["first_break"].isna() & s["note"].str.contains("no break", na=False)]["label"]) or "—"
        lines.append(f"| {iss.short} | {order} | {none} |")
    return "\n".join(lines)
