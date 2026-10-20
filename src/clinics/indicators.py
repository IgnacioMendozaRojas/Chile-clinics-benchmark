"""Financial indicators from canonical statements.

Ratios are computed in nominal terms: they divide figures of the same period, so
inflation cancels. Flow figures are last-twelve-months (LTM) to remove seasonality;
balances are at the quarter end; averages use the quarter end and four quarters earlier.

Definitions (also rendered in the README):

* operating result (EBIT) = gross profit - distribution costs - administrative expenses
  - credit losses presented on the face of the income statement (IFRS 9). Other income,
  other expenses and other gains/losses are excluded as largely non-recurring; credit
  losses are included so issuers that present them inside administrative expenses and
  issuers that present them on a separate line are measured alike.
* EBITDA = EBIT + depreciation and amortisation (including right-of-use assets from 2019,
  when IFRS 16 moved lease costs out of operating expenses — a level break for every issuer).
* gross financial debt = other financial liabilities + lease liabilities (current and
  non-current); net debt = gross financial debt - cash and cash equivalents.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .fetch import Quarter

# Components that are legitimately absent for some issuers (no such line): treated as 0.
ZERO_IF_MISSING = {
    "distribution_costs",
    "impairment_credit_losses",
    "lease_liabilities_current",
    "lease_liabilities_noncurrent",
    "inventories",
    "other_financial_liabilities_current",
    "other_financial_liabilities_noncurrent",
}

INDICATORS = {
    # name: (group, label, unit)
    "revenue_ltm": ("Scale", "Revenue, LTM", "CLP bn"),
    "revenue_growth": ("Profitability", "Revenue growth, LTM vs year earlier", "%"),
    "gross_margin": ("Profitability", "Gross margin", "%"),
    "ebit_margin": ("Profitability", "Operating (EBIT) margin", "%"),
    "ebitda_margin": ("Profitability", "EBITDA margin", "%"),
    "net_margin": ("Profitability", "Net margin", "%"),
    "roe": ("Profitability", "Return on average equity", "%"),
    "roa": ("Profitability", "Return on average assets", "%"),
    "cost_of_sales_ratio": ("Cost structure", "Cost of sales / revenue", "%"),
    "admin_ratio": ("Cost structure", "Administrative expenses / revenue", "%"),
    "da_ratio": ("Cost structure", "Depreciation and amortisation / revenue", "%"),
    "current_ratio": ("Liquidity", "Current ratio", "x"),
    "quick_ratio": ("Liquidity", "Quick ratio", "x"),
    "cash_to_current_debt": ("Liquidity", "Cash / current financial debt", "x"),
    "cfo_margin": ("Liquidity", "Operating cash flow / revenue", "%"),
    "liabilities_to_equity": ("Leverage", "Total liabilities / equity", "x"),
    "net_debt_to_ebitda": ("Leverage", "Net financial debt / EBITDA", "x"),
    "interest_coverage": ("Leverage", "EBITDA / finance costs", "x"),
    "current_debt_share": ("Leverage", "Current share of financial debt", "%"),
    "dso": ("Working capital", "Days sales outstanding (trade and other receivables)", "days"),
    "dpo": ("Working capital", "Days payables outstanding (trade and other payables)", "days"),
    "credit_loss_ratio": ("Working capital", "Credit losses / revenue", "%"),
    "capex_ratio": ("Investment", "Capex (PP&E purchases) / revenue", "%"),
}


def _wide(statements: pd.DataFrame) -> pd.DataFrame:
    """issuer x period frame: stocks as balances, flows as LTM. Columns are variable names."""
    s = statements.copy()
    s["x"] = np.where(s["kind"] == "flow", s["ltm"], s["value"])
    w = s.pivot_table(index=["issuer", "period"], columns="variable", values="x", aggfunc="first", dropna=False)
    return w.sort_index()


def _col(w: pd.DataFrame, name: str) -> pd.Series:
    if name in w:
        col = w[name]
        return col.fillna(0.0) if name in ZERO_IF_MISSING else col
    return pd.Series(0.0 if name in ZERO_IF_MISSING else np.nan, index=w.index)


def _lag(series: pd.Series, n: int = 4) -> pd.Series:
    """Value n quarters earlier for the same issuer (NaN when that quarter was not filed)."""
    issuers = series.index.get_level_values("issuer")
    earlier = [str(Quarter.parse(p).shift(-n)) for p in series.index.get_level_values("period")]
    values = series.reindex(pd.MultiIndex.from_arrays([issuers, earlier], names=series.index.names))
    return pd.Series(values.to_numpy(), index=series.index)


def _avg(series: pd.Series) -> pd.Series:
    return (series + _lag(series)) / 2


def _div(a: pd.Series, b: pd.Series) -> pd.Series:
    return a / b.where(b.abs() > 0)


def compute(statements: pd.DataFrame) -> pd.DataFrame:
    """Long table: issuer, period, indicator, value."""
    w = _wide(statements)
    c = lambda name: _col(w, name)  # noqa: E731

    revenue = c("revenue")
    ebit = c("gross_profit") - c("distribution_costs") - c("administrative_expense") - c("impairment_credit_losses")
    ebitda = ebit + c("depreciation_amortisation")
    current_debt = c("other_financial_liabilities_current") + c("lease_liabilities_current")
    gross_debt = current_debt + c("other_financial_liabilities_noncurrent") + c("lease_liabilities_noncurrent")
    net_debt = gross_debt - c("cash")
    equity = c("equity")

    out = {
        "revenue_ltm": revenue / 1e9,
        "revenue_growth": _div(revenue, _lag(revenue)) - 1,
        "gross_margin": _div(c("gross_profit"), revenue),
        "ebit_margin": _div(ebit, revenue),
        "ebitda_margin": _div(ebitda, revenue),
        "net_margin": _div(c("profit_loss"), revenue),
        "roe": _div(c("profit_loss"), _avg(equity)),
        "roa": _div(c("profit_loss"), _avg(c("assets"))),
        "cost_of_sales_ratio": _div(c("cost_of_sales"), revenue),
        "admin_ratio": _div(c("administrative_expense"), revenue),
        "da_ratio": _div(c("depreciation_amortisation"), revenue),
        "current_ratio": _div(c("current_assets"), c("current_liabilities")),
        "quick_ratio": _div(c("current_assets") - c("inventories"), c("current_liabilities")),
        "cash_to_current_debt": _div(c("cash"), current_debt),
        "cfo_margin": _div(c("operating_cash_flow"), revenue),
        "liabilities_to_equity": _div(c("liabilities"), equity),
        "net_debt_to_ebitda": _div(net_debt, ebitda.where(ebitda > 0)),
        "interest_coverage": _div(ebitda, c("finance_costs")),
        "current_debt_share": _div(current_debt, gross_debt),
        "dso": _div(c("trade_receivables_current"), revenue) * 365,
        "dpo": _div(c("trade_payables_current"), c("cost_of_sales")) * 365,
        "credit_loss_ratio": _div(c("impairment_credit_losses"), revenue).where(
            w.get("impairment_credit_losses", pd.Series(np.nan, index=w.index)).notna()
        ),
        "capex_ratio": _div(c("capex"), revenue),
    }
    frame = pd.DataFrame(out, index=w.index).replace([np.inf, -np.inf], np.nan)
    long = frame.stack(future_stack=True).rename("value").reset_index().rename(columns={"level_2": "indicator"})
    return long.dropna(subset=["value"]).reset_index(drop=True)
