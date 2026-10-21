"""Credit losses: cost of risk and EBITDA before credit losses.

Only Indisa files the IFRS 9 credit-loss expense consistently (a line of its income statement
since 2019Q4); CLC adds that line in 2025Q4 and Banmédica in a single quarter, and no issuer
tags the expense in its notes. Everywhere else the LTM charge is estimated from the allowance
roll-forward:

    charge (LTM) = allowance(q) - allowance(q-4) + write-offs (LTM)

using the allowance from the CMF receivables note. Write-offs are filed by RedSalud only, in
24 of its 42 filings; without them the estimate is a lower bound (receivables written off and
already provided for are not counted). `method` records which source each figure uses:

* face                    IFRS 9 line of the income statement (issuers with credit_losses: face)
* allowance+write-offs    roll-forward with write-offs
* allowance               allowance change only, floored at zero — a lower bound

Where both exist (Indisa), the allowance change is 40-55% below the IFRS 9 expense: write-offs
of receivables already provided for are what the proxy misses.

cost_of_risk = charge / revenue (both LTM).
ebitda_before_credit_losses_margin = (EBITDA + charge) / revenue: EBITDA already deducts the
face line where an issuer files it, and the rest of the charge sits in cost of sales or
administrative expenses, so adding the whole charge back removes credit losses exactly once.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import _col, _lag, _wide, ebitda

INDICATORS = {
    "cost_of_risk": ("Credit losses", "Cost of risk: credit losses / revenue, LTM", "%"),
    "ebitda_before_credit_losses_margin": ("Profitability", "EBITDA margin before credit losses, LTM", "%"),
}
COLUMNS = [
    "issuer",
    "period",
    "method",
    "charge_ltm",
    "face_ltm",
    "allowance",
    "allowance_change_4q",
    "write_offs_ltm",
    "revenue_ltm",
    "ebitda_ltm",
]


def compute(statements: pd.DataFrame, note_table: pd.DataFrame, issuers) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(indicators in long format, detail table with the method used per issuer and quarter)."""
    w = _wide(statements)
    revenue, eb = _col(w, "revenue"), ebitda(w)
    face = w["impairment_credit_losses"] if "impairment_credit_losses" in w else pd.Series(np.nan, index=w.index)
    wo = w["receivable_write_offs"] if "receivable_write_offs" in w else pd.Series(np.nan, index=w.index)
    allow = note_table[(note_table["note"] == "receivables_allowance") & (note_table["item"] == "allowance")]
    allow = allow.set_index(["issuer", "period"])["value"]
    allow = allow[~allow.index.duplicated()].reindex(w.index)
    change = allow - _lag(allow)

    uses_face = pd.Series(
        [issuers[i].credit_losses == "face" if i in issuers else False for i in w.index.get_level_values("issuer")],
        index=w.index,
    )
    method = pd.Series(np.where(wo.notna(), "allowance+write-offs", "allowance"), index=w.index)
    charge = change + wo.fillna(0)
    charge = charge.where(change.notna())
    # without write-offs the allowance can fall when receivables are written off; the true charge is
    # never negative, so the lower bound is floored at zero
    charge = charge.where(wo.notna(), charge.clip(lower=0))
    face_ok = uses_face & face.notna()
    charge = charge.where(~face_ok, face)
    method = method.where(~face_ok, "face").where(charge.notna(), "")

    detail = pd.DataFrame(
        {
            "method": method,
            "charge_ltm": charge,
            "face_ltm": face,
            "allowance": allow,
            "allowance_change_4q": change,
            "write_offs_ltm": wo,
            "revenue_ltm": revenue,
            "ebitda_ltm": eb,
        }
    ).reset_index()
    detail = detail[detail["method"] != ""][COLUMNS].reset_index(drop=True)

    valid = revenue.where(revenue > 0)
    parts = {
        "cost_of_risk": charge / valid,
        "ebitda_before_credit_losses_margin": (eb + charge) / valid,
    }
    frames = [s.rename("value").reset_index().assign(indicator=n) for n, s in parts.items()]
    out = pd.concat(frames, ignore_index=True).replace([np.inf, -np.inf], np.nan).dropna(subset=["value"])
    return out[["issuer", "period", "indicator", "value"]].reset_index(drop=True), detail
