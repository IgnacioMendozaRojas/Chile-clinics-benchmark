"""Automatic checks on the canonical statements.

Severity:
* error   — the data cannot be trusted; the update workflow does not commit
* warning — published, but listed in the report for review
* info    — logged context (e.g. restated figures)
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .fetch import Quarter

BALANCE_TOLERANCE = 0.001  # 0.1% of total assets
ARITHMETIC_TOLERANCE = 0.005  # 0.5% of revenue
ASSET_JUMP = 0.30  # quarter-on-quarter change in total assets
REVENUE_JUMP = 0.50  # quarterly revenue vs same quarter a year earlier
CORE = [
    "assets",
    "liabilities",
    "equity",
    "current_assets",
    "current_liabilities",
    "cash",
    "revenue",
    "cost_of_sales",
    "gross_profit",
    "administrative_expense",
    "profit_loss",
    "trade_receivables_current",
]


def _finding(severity, check, issuer, period, message, value=np.nan):
    return {
        "severity": severity,
        "check": check,
        "issuer": issuer,
        "period": period,
        "value": value,
        "message": message,
    }


def run(statements: pd.DataFrame, restated: pd.DataFrame) -> pd.DataFrame:
    s = statements
    stock = s.pivot_table(index=["issuer", "period"], columns="variable", values="value", aggfunc="first")
    quarter = s[s["kind"] == "flow"].pivot_table(
        index=["issuer", "period"], columns="variable", values="quarter", aggfunc="first"
    )
    ytd = s[s["kind"] == "flow"].pivot_table(
        index=["issuer", "period"], columns="variable", values="value", aggfunc="first"
    )
    g = lambda frame, name, key: frame[name].get(key, np.nan) if name in frame else np.nan  # noqa: E731
    out = []

    for key in stock.index:
        issuer, period = key
        assets = g(stock, "assets", key)

        # 1. balance sheet balances
        eq_li = g(stock, "equity_and_liabilities", key)
        if math.isnan(eq_li):
            eq_li = g(stock, "liabilities", key) + g(stock, "equity", key)
        if math.isnan(assets) or math.isnan(eq_li):
            out.append(_finding("error", "balance", issuer, period, "total assets or liabilities + equity missing"))
        elif abs(assets - eq_li) > BALANCE_TOLERANCE * abs(assets):
            gap = (assets - eq_li) / assets
            out.append(_finding("error", "balance", issuer, period, "assets != liabilities + equity", gap))

        # 2. assets = current + non-current
        split = g(stock, "current_assets", key) + g(stock, "noncurrent_assets", key)
        if not math.isnan(split) and not math.isnan(assets) and abs(assets - split) > BALANCE_TOLERANCE * abs(assets):
            out.append(
                _finding(
                    "warning", "asset_split", issuer, period, "assets != current + non-current", split / assets - 1
                )
            )

        # 3. income statement arithmetic (year to date)
        rev = g(ytd, "revenue", key)
        gp_calc = rev - g(ytd, "cost_of_sales", key)
        gp = g(ytd, "gross_profit", key)
        if not any(math.isnan(x) for x in (rev, gp, gp_calc)) and abs(gp - gp_calc) > ARITHMETIC_TOLERANCE * abs(rev):
            out.append(
                _finding(
                    "warning", "gross_profit", issuer, period, "gross profit != revenue - cost of sales", gp - gp_calc
                )
            )
        pbt, tax, pl = (g(ytd, n, key) for n in ("profit_before_tax", "income_tax", "profit_loss"))
        if not any(math.isnan(x) for x in (pbt, tax, pl, rev)):
            if min(abs(pl - (pbt - tax)), abs(pl - (pbt + tax))) > ARITHMETIC_TOLERANCE * abs(rev):
                out.append(_finding("warning", "net_income", issuer, period, "profit != profit before tax -/+ tax"))

        # 4. missing core variables
        missing = [v for v in CORE if math.isnan(g(stock, v, key)) and math.isnan(g(ytd, v, key))]
        if missing:
            out.append(_finding("warning", "missing", issuer, period, "missing: " + ", ".join(missing), len(missing)))

        # 5. jumps
        prev_key = (issuer, str(Quarter.parse(period).shift(-1)))
        prev_assets = g(stock, "assets", prev_key)
        if not math.isnan(prev_assets) and prev_assets and abs(assets / prev_assets - 1) > ASSET_JUMP:
            out.append(
                _finding(
                    "warning", "jump_assets", issuer, period, "total assets moved > 30% q/q", assets / prev_assets - 1
                )
            )
        q_rev = g(quarter, "revenue", key)
        yago = g(quarter, "revenue", (issuer, str(Quarter.parse(period).shift(-4))))
        if not math.isnan(q_rev) and q_rev < 0:
            out.append(_finding("warning", "negative_quarter", issuer, period, "negative quarterly revenue", q_rev))
        elif not math.isnan(q_rev) and not math.isnan(yago) and yago > 0 and abs(q_rev / yago - 1) > REVENUE_JUMP:
            out.append(
                _finding(
                    "warning", "jump_revenue", issuer, period, "quarterly revenue moved > 50% y/y", q_rev / yago - 1
                )
            )

    # 6. restatements (context, not a problem in itself)
    if not restated.empty:
        for (issuer, end), grp in restated.groupby(["issuer", "end"]):
            period = str(Quarter.of(end))
            concepts = ", ".join(sorted(c.split(":")[-1] for c in grp["concept"]))
            out.append(
                _finding("info", "restated", issuer, period, f"{len(grp)} figures restated: {concepts}", len(grp))
            )

    cols = ["severity", "check", "issuer", "period", "value", "message"]
    return pd.DataFrame(out, columns=cols).sort_values(["severity", "issuer", "period", "check"]).reset_index(drop=True)


def report(findings: pd.DataFrame, coverage: pd.DataFrame) -> str:
    """Markdown summary for data/processed/validation_report.md."""
    lines = ["# Validation report", ""]
    counts = findings["severity"].value_counts()
    lines.append(
        f"**{counts.get('error', 0)} errors, {counts.get('warning', 0)} warnings, {counts.get('info', 0)} info.**"
    )
    lines += ["", "## Coverage", "", "| Issuer | Quarters | First | Last |", "|---|---|---|---|"]
    for r in coverage.itertuples(index=False):
        lines.append(f"| {r.issuer} | {r.quarters} | {r.first} | {r.last} |")
    for severity in ("error", "warning", "info"):
        f = findings[findings["severity"] == severity]
        if f.empty:
            continue
        lines += [
            "",
            f"## {severity.capitalize()}s",
            "",
            "| Check | Issuer | Period | Value | Detail |",
            "|---|---|---|---|---|",
        ]
        for r in f.itertuples(index=False):
            value = "" if pd.isna(r.value) else (f"{r.value:.2%}" if abs(r.value) < 10 else f"{r.value:,.0f}")
            lines.append(f"| {r.check} | {r.issuer} | {r.period} | {value} | {r.message} |")
    return "\n".join(lines) + "\n"
