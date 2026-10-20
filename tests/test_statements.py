import datetime as dt
import math

import pandas as pd
import pytest

from clinics import indicators, statements, validate
from clinics.facts import latest
from clinics.fetch import Quarter

V = {
    "revenue": statements.Variable("revenue", "flow", ("ifrs-full:Revenue",)),
    "cost_of_sales": statements.Variable("cost_of_sales", "flow", ("ifrs-full:CostOfSales",), sign="expense"),
    "assets": statements.Variable("assets", "stock", ("ifrs-full:Assets",)),
    "equity": statements.Variable(
        "equity", "stock", ("ifrs-full:Equity",), overrides={"x": ("ifrs-full:EquityAttributableToOwnersOfParent",)}
    ),
}


def fact(filing, concept, end, value, start=None):
    return {
        "issuer": "clc",
        "filing": filing,
        "concept": concept,
        "start": start,
        "end": end,
        "instant": start is None,
        "value": value,
        "decimals": "0",
    }


def ytd_rows(filing, year, month, revenue, cost):
    end = (pd.Timestamp(year, month, 1) + pd.offsets.MonthEnd(0)).date()
    jan1 = dt.date(year, 1, 1)
    return [
        fact(filing, "ifrs-full:Revenue", end, revenue, jan1),
        fact(filing, "ifrs-full:CostOfSales", end, -cost, jan1),  # negative sign convention
        fact(filing, "ifrs-full:Assets", end, 1000 + month),
    ]


@pytest.fixture
def facts():
    rows = []
    # 2024: quarterly revenue 100, 110, 120, 130 (YTD 100, 210, 330, 460); cost = 60% of revenue
    for q, ytd in zip((1, 2, 3, 4), (100, 210, 330, 460), strict=True):
        rows += ytd_rows(f"2024Q{q}", 2024, 3 * q, ytd, 0.6 * ytd)
    # 2025Q1 and Q2; the 2025Q2 filing restates the 2024 H1 comparative (210 -> 212)
    rows += ytd_rows("2025Q1", 2025, 3, 140, 84) + ytd_rows("2025Q1", 2024, 3, 100, 60)
    rows += ytd_rows("2025Q2", 2025, 6, 290, 174) + ytd_rows("2025Q2", 2024, 6, 212, 127.2)
    return pd.DataFrame(rows)


def test_latest_keeps_restated_value_and_first_report(facts):
    lat = latest(facts)
    h1 = lat[(lat["concept"] == "ifrs-full:Revenue") & (lat["end"] == dt.date(2024, 6, 30))].iloc[0]
    assert (h1["value"], h1["filing"], h1["first_value"], h1["first_filing"]) == (212, "2025Q2", 210, "2024Q2")
    assert lat.duplicated(["concept", "start", "end"]).sum() == 0


def test_build_decumulates_quarters_and_ltm(facts):
    periods = {"clc": [Quarter(2024, q) for q in (1, 2, 3, 4)] + [Quarter(2025, 1), Quarter(2025, 2)]}
    st = statements.build(latest(facts), periods, V).set_index(["period", "variable"])
    rev = st.xs("revenue", level="variable")
    assert rev.loc["2024Q4", "ltm"] == 460
    assert rev.loc["2024Q2", "quarter"] == 112  # restated H1 (212) minus Q1 (100)
    assert rev.loc["2025Q2", "quarter"] == 150
    assert rev.loc["2025Q2", "ltm"] == 290 + 460 - 212  # uses the restated comparative
    assert rev.loc["2024Q2", "restated"] and not rev.loc["2024Q1", "restated"]
    cost = st.xs("cost_of_sales", level="variable")
    assert cost.loc["2025Q2", "value"] == 174  # sign normalised
    assert st.loc[("2025Q2", "assets"), "value"] == 1006
    assert math.isnan(rev.loc["2024Q1", "ltm"])  # no 2023 data
    assert st.loc[("2024Q1", "equity"), "concept"] != st.loc[("2024Q1", "equity"), "concept"]  # missing -> NaN


def test_restatements_table(facts):
    r = statements.restatements(latest(facts), V)
    assert set(r["concept"]) == {"ifrs-full:Revenue", "ifrs-full:CostOfSales"}
    pct = r.set_index("concept")["change_pct"]
    assert pct["ifrs-full:Revenue"] == pytest.approx(2 / 210)
    assert pct["ifrs-full:CostOfSales"] == pytest.approx(-1.2 / 126)  # filed with negative sign


def test_indicators_definitions():
    rows = []
    for period, scale in (("2024Q2", 1.0), ("2025Q2", 1.1)):
        for var, kind, v in [
            ("revenue", "flow", 1000),
            ("gross_profit", "flow", 300),
            ("administrative_expense", "flow", 150),
            ("depreciation_amortisation", "flow", 50),
            ("profit_loss", "flow", 60),
            ("finance_costs", "flow", 25),
            ("cash", "stock", 100),
            ("other_financial_liabilities_current", "stock", 100),
            ("other_financial_liabilities_noncurrent", "stock", 300),
            ("equity", "stock", 500),
            ("trade_receivables_current", "stock", 250),
            ("current_assets", "stock", 400),
            ("current_liabilities", "stock", 320),
        ]:
            x = v * scale
            rows.append({"issuer": "clc", "period": period, "variable": var, "kind": kind, "value": x, "ltm": x})
    ind = indicators.compute(pd.DataFrame(rows))
    got = ind[ind["period"] == "2025Q2"].set_index("indicator")["value"]
    assert got["ebit_margin"] == pytest.approx(0.15)  # no distribution costs or credit-loss line -> 0
    assert got["ebitda_margin"] == pytest.approx(0.20)
    assert got["net_debt_to_ebitda"] == pytest.approx(300 / 200)
    assert got["interest_coverage"] == pytest.approx(8)
    assert got["dso"] == pytest.approx(250 / 1000 * 365)
    assert got["revenue_growth"] == pytest.approx(0.10)
    assert got["roe"] == pytest.approx(66 / ((550 + 500) / 2))
    assert "credit_loss_ratio" not in got  # line not filed: not reported as zero


def test_validation_flags_unbalanced_sheet():
    st = pd.DataFrame(
        [
            {"issuer": "clc", "period": "2025Q2", "variable": "assets", "kind": "stock", "value": 1000},
            {"issuer": "clc", "period": "2025Q2", "variable": "liabilities", "kind": "stock", "value": 600},
            {"issuer": "clc", "period": "2025Q2", "variable": "equity", "kind": "stock", "value": 390},
            {"issuer": "clc", "period": "2025Q2", "variable": "revenue", "kind": "flow", "value": 500, "quarter": 250},
        ]
    )
    f = validate.run(st, pd.DataFrame())
    errors = f[f["severity"] == "error"]
    assert list(errors["check"]) == ["balance"]
    assert errors["value"].iloc[0] == pytest.approx(0.01)
    assert "missing" in set(f["check"])
