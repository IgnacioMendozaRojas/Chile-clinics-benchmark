import pandas as pd
import pytest

from clinics import figures
from clinics.issuers import load_issuers


def _st(period, variable, kind, value, revised=None):
    return {
        "issuer": "clc",
        "period": period,
        "variable": variable,
        "kind": kind,
        "value": value,
        "ltm": value,
        "restated": revised is not None,
        "revised": revised,
    }


def test_revised_indicators_only_material_and_flows_only_in_q4():
    st = pd.DataFrame(
        [
            _st("2024Q4", "trade_receivables_current", "stock", 85, revised=26),
            _st("2024Q4", "revenue", "flow", 172, revised=153),
            _st("2024Q4", "current_assets", "stock", 100, revised=100.5),  # 0.5%: not material
            _st("2024Q4", "current_liabilities", "stock", 100),
            _st("2025Q2", "trade_receivables_current", "stock", 90),
            _st("2025Q2", "revenue", "flow", 150, revised=120),  # flow outside Q4: ignored
        ]
    )
    rv = figures.revised_indicators(st).set_index(["indicator", "period"])
    assert rv.loc[("dso", "2024Q4"), "revised"] == pytest.approx(26 / 153 * 365)
    assert rv.loc[("dso", "2024Q4"), "as_reported"] == pytest.approx(85 / 172 * 365)
    assert ("dso", "2025Q2") not in rv.index and ("current_ratio", "2024Q4") not in rv.index


def test_plot_writes_png(tmp_path):
    ind = pd.DataFrame(
        [
            {"issuer": k, "period": p, "indicator": "dso", "value": v}
            for k in ("clc", "indisa")
            for p, v in (("2024Q4", 100), ("2025Q1", 110))
        ]
    )
    rv = pd.DataFrame([{"issuer": "clc", "period": "2024Q4", "as_reported": 100, "revised": 60, "indicator": "dso"}])
    path = figures.plot(ind, rv, "dso", tmp_path, load_issuers())
    assert path.read_bytes()[:4] == b"\x89PNG"
