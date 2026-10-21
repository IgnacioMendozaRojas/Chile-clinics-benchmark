import datetime as dt
import json

import pandas as pd
import pytest

from clinics import build, credit, sequence, validate
from clinics.issuers import load_issuers


def _st(issuer, period, variable, value, kind="flow"):
    return {"issuer": issuer, "period": period, "variable": variable, "kind": kind, "value": value, "ltm": value}


def _allow(issuer, period, value):
    return {"issuer": issuer, "period": period, "note": "receivables_allowance", "item": "allowance", "value": value}


def test_credit_charge_methods():
    rows, notes = [], []
    for p, allow in (("2024Q2", 20), ("2025Q2", 50)):
        for issuer in ("clc", "indisa", "redsalud"):
            rows += [
                _st(issuer, p, "revenue", 1000),
                _st(issuer, p, "gross_profit", 300),
                _st(issuer, p, "administrative_expense", 200),
                _st(issuer, p, "depreciation_amortisation", 40),
            ]
            notes.append(_allow(issuer, p, allow))
    rows += [
        _st("indisa", "2025Q2", "impairment_credit_losses", 12),
        _st("redsalud", "2025Q2", "receivable_write_offs", 5),
    ]
    ind, detail = credit.compute(pd.DataFrame(rows), pd.DataFrame(notes), load_issuers())
    d = detail[detail["period"] == "2025Q2"].set_index("issuer")
    assert d.loc["clc", "method"] == "allowance" and d.loc["clc", "charge_ltm"] == 30
    assert d.loc["redsalud", "method"] == "allowance+write-offs" and d.loc["redsalud", "charge_ltm"] == 35
    assert d.loc["indisa", "method"] == "face" and d.loc["indisa", "charge_ltm"] == 12
    v = ind[ind["period"] == "2025Q2"].set_index(["issuer", "indicator"])["value"]
    assert v[("clc", "cost_of_risk")] == pytest.approx(0.03)
    assert v[("clc", "ebitda_before_credit_losses_margin")] == pytest.approx((140 + 30) / 1000)
    # Indisa's EBITDA already deducts the face line (140 - 12), adding it back gives 140
    assert v[("indisa", "ebitda_before_credit_losses_margin")] == pytest.approx(0.14)


def test_allowance_only_charge_is_floored_at_zero():
    rows = [_st("clc", p, "revenue", 1000) for p in ("2024Q2", "2025Q2")]
    rows += [_st("redsalud", p, "revenue", 1000) for p in ("2024Q2", "2025Q2")]
    rows.append(_st("redsalud", "2025Q2", "receivable_write_offs", 5))
    notes = [_allow(i, p, a) for i in ("clc", "redsalud") for p, a in (("2024Q2", 50), ("2025Q2", 40))]
    _, detail = credit.compute(pd.DataFrame(rows), pd.DataFrame(notes), load_issuers())
    d = detail[detail["period"] == "2025Q2"].set_index("issuer")["charge_ltm"]
    assert d["clc"] == 0  # allowance fell by 10, no write-offs filed: lower bound 0, not -10
    assert d["redsalud"] == -5  # -10 + 5 written off: a net release, kept


def _series(issuer, indicator, values, start=(2017, 1)):
    y, q = start
    out = []
    for v in values:
        out.append({"issuer": issuer, "period": f"{y}Q{q}", "indicator": indicator, "value": v})
        y, q = (y, q + 1) if q < 4 else (y + 1, 1)
    return out


def test_sequence_needs_two_consecutive_adverse_quarters():
    base = [1.0, 1.2] * 10  # 2017Q1-2021Q4, range [1.0, 1.2]
    rows = _series("clc", "dso", base + [1.3, 1.1, 1.4, 1.5, 1.6])  # one-off in 2022Q1, break from 2022Q3
    rows += _series("clc", "current_ratio", base + [1.3, 1.4, 1.5])  # up is not adverse for liquidity
    s = sequence.breaks(pd.DataFrame(rows)).set_index("indicator")
    assert s.loc["dso", "first_break"] == "2022Q3" and s.loc["dso", "quarters_from_ruling"] == -1
    assert pd.isna(s.loc["current_ratio", "first_break"]) and "no break" in s.loc["current_ratio", "note"]


def test_sequence_marks_short_and_missing_post_crisis_data():
    rows = _series("bupa", "dso", [1.0] * 21)  # 2017Q1-2022Q1: one quarter after 2021
    rows += _series("clc", "receivables_over_180_share", [0.1] * 4 + [0.9, 0.9], start=(2021, 1))
    s = sequence.breaks(pd.DataFrame(rows)).set_index(["issuer", "indicator"])
    assert "too few quarters" in s.loc[("bupa", "dso"), "note"]
    assert "short baseline" in s.loc[("clc", "receivables_over_180_share"), "note"]
    assert s.loc[("clc", "receivables_over_180_share"), "first_break"] == "2022Q1"


def test_known_events_explain_findings_and_flag_stale_ones():
    f = pd.DataFrame(
        [
            {
                "severity": "warning",
                "check": "jump_assets",
                "issuer": "indisa",
                "period": "2025Q4",
                "value": 0.35,
                "message": "x",
            },
            {
                "severity": "warning",
                "check": "jump_revenue",
                "issuer": "clc",
                "period": "2021Q2",
                "value": 0.6,
                "message": "y",
            },
        ]
    )
    events = [
        {"issuer": "indisa", "period": "2025Q4", "check": "jump_assets", "explanation": "Revaluation.", "source": "s"},
        {"issuer": "redsalud", "period": "2021Q2", "check": "jump_revenue", "explanation": "COVID.", "source": "s"},
    ]
    out = validate.explain(f, events)
    assert out.loc[out["issuer"] == "indisa", "severity"].item() == "explained"
    assert out.loc[out["issuer"] == "clc", "severity"].item() == "warning"
    assert out.loc[out["check"] == "stale_event", "issuer"].item() == "redsalud"
    cov = pd.DataFrame([{"issuer": "indisa", "quarters": 1, "first": "2025Q4", "last": "2025Q4"}])
    text = validate.report(out, cov)
    assert "1 explained" in text and "| jump_assets | indisa | 2025Q4 | 35.00% | Revaluation. | s |" in text


def test_shipped_known_events_are_well_formed():
    for e in validate.load_events():
        assert {"issuer", "period", "check", "explanation", "source"} <= set(e)
        assert e["issuer"] in load_issuers()


def test_build_latest_json_and_readme(tmp_path):
    rows = []
    for issuer, cov in (("clc", (0.40, 0.48)), ("indisa", (0.17, 0.16)), ("redsalud", (0.18, 0.19))):
        for period, v in zip(("2026Q1", "2026Q2"), cov, strict=True):
            rows += [
                {"issuer": issuer, "period": period, "indicator": "allowance_coverage", "value": v},
                {"issuer": issuer, "period": period, "indicator": "cost_of_risk", "value": v / 10},
            ]
    rows = [r for r in rows if not (r["issuer"] == "redsalud" and r["period"] == "2026Q2")]  # RedSalud lags
    pd.DataFrame(rows).to_csv(tmp_path / "indicators.csv", index=False)
    pd.DataFrame(
        [
            {"issuer": k, "indicator": "allowance_coverage", "baseline_high": h}
            for k, h in (("clc", 0.18), ("indisa", 0.17), ("redsalud", 0.34))
        ]
    ).to_csv(tmp_path / "sequence.csv", index=False)
    readme = tmp_path / "README.md"
    readme.write_text("intro\n<!-- latest:start -->\nold\n<!-- latest:end -->\nrest\n")
    data = build.build(tmp_path, readme, today=dt.date(2026, 10, 21))
    assert data["schema"] == 1 and data["as_of"] == "2026Q2" and data["frequency"] == "quarterly"
    m = {x["id"]: x for x in data["metrics"]}
    assert m["clc_allowance_coverage"]["change"] == pytest.approx(0.08)
    assert m["clc_allowance_coverage"]["change_type"] == "points"
    assert m["redsalud_allowance_coverage"]["period"] == "2026Q1"
    assert data["headline"].startswith(
        "In 2026Q2, CLC's allowance covered 48% of its gross trade receivables (at most 18%"
    )
    text = readme.read_text()
    assert "**Latest reading: 2026Q2.**" in text and "latest is 2026Q1" in text and "+8.0 pp" in text
    assert text.startswith("intro\n") and text.endswith("rest\n") and "old" not in text
    # nothing new: the date and the files stay as they were
    again = build.build(tmp_path, readme, today=dt.date(2026, 11, 1))
    assert again["updated"] == "2026-10-21"
    assert json.loads((tmp_path / "latest.json").read_text())["updated"] == "2026-10-21"
