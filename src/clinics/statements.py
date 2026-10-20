"""Canonical quarterly statements: one value per issuer x quarter x variable.

Input is `facts.latest()` (most recent filed figure per concept and period). For each
variable in `config/concepts.yaml` the first listed tag the issuer reports is used.

* stock variables: balance at the quarter end
* flow variables: Chilean filings report flows year to date (Jan-Mar, Jan-Jun, ...), so
  `ytd` is what was filed, `quarter` = YTD(q) - YTD(q-1) and
  `ltm` (last twelve months) = YTD(q) + FY(y-1) - YTD(q, y-1), all with the same tag.
* `sign: expense` takes the absolute value (filers use both sign conventions for costs).
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml

from .fetch import Quarter

CONFIG = Path(__file__).resolve().parents[2] / "config" / "concepts.yaml"
if not CONFIG.exists():  # installed wheel
    CONFIG = Path(__file__).resolve().parent / "_config" / "concepts.yaml"

RESTATEMENT_TOLERANCE = 0.001  # 0.1% of the first-reported value


@dataclass(frozen=True)
class Variable:
    name: str
    kind: str  # stock | flow
    tags: tuple[str, ...]
    sign: str = "as_reported"  # as_reported | expense
    label: str = ""
    overrides: dict = field(default_factory=dict)  # issuer -> tags, when one issuer files it elsewhere

    def tags_for(self, issuer: str) -> tuple[str, ...]:
        return tuple(self.overrides.get(issuer, self.tags))


def load_variables(path: Path = CONFIG) -> dict[str, Variable]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))["variables"]
    out = {}
    for name, spec in raw.items():
        out[name] = Variable(
            name=name,
            kind=spec["kind"],
            tags=tuple(spec["tags"]),
            sign=spec.get("sign", "as_reported"),
            label=spec.get("label", name),
            overrides={k: tuple(v) for k, v in (spec.get("overrides") or {}).items()},
        )
    return out


class _Lookup:
    """Fast (concept, start, end) -> fact row access for one issuer."""

    def __init__(self, latest: pd.DataFrame):
        self.rows = {}
        for r in latest.itertuples(index=False):
            start = r.start if isinstance(r.start, dt.date) else None
            self.rows[(r.concept, start, r.end)] = r

    def get(self, concept: str, start: dt.date | None, end: dt.date):
        return self.rows.get((concept, start, end))


def _signed(value: float, var: Variable) -> float:
    return abs(value) if var.sign == "expense" else value


def _restated(row) -> bool:
    if row is None or row.first_value is None or (isinstance(row.first_value, float) and math.isnan(row.first_value)):
        return False
    base = max(abs(row.first_value), 1.0)
    return abs(row.value - row.first_value) / base > RESTATEMENT_TOLERANCE


def _ytd(lk: _Lookup, tag: str, q: Quarter):
    return lk.get(tag, dt.date(q.year, 1, 1), q.end)


def build(latest: pd.DataFrame, periods: dict[str, list[Quarter]], variables: dict[str, Variable]) -> pd.DataFrame:
    """Long table: issuer, period, variable, kind, value, quarter, ltm, concept, filing, restated.

    `periods` maps each issuer to the quarters it actually filed (statements are built only there).
    `value` is the balance (stocks) or the year-to-date figure (flows).
    """
    rows = []
    for issuer, quarters in periods.items():
        lk = _Lookup(latest[latest["issuer"] == issuer])
        for q in quarters:
            for var in variables.values():
                row = {"issuer": issuer, "period": str(q), "period_end": q.end, "variable": var.name, "kind": var.kind}
                if var.kind == "stock":
                    hit = next(
                        ((t, lk.get(t, None, q.end)) for t in var.tags_for(issuer) if lk.get(t, None, q.end)), None
                    )
                    if hit is None:
                        rows.append(row)
                        continue
                    tag, fact = hit
                    rows.append(
                        {
                            **row,
                            "value": _signed(fact.value, var),
                            "concept": tag,
                            "filing": fact.filing,
                            "restated": _restated(fact),
                        }
                    )
                    continue
                hit = next(((t, _ytd(lk, t, q)) for t in var.tags_for(issuer) if _ytd(lk, t, q)), None)
                if hit is None:
                    rows.append(row)
                    continue
                tag, fact = hit
                ytd = _signed(fact.value, var)
                if q.q == 1:
                    quarter = ytd
                else:
                    prev = _ytd(lk, tag, q.shift(-1))
                    quarter = ytd - _signed(prev.value, var) if prev else math.nan
                if q.q == 4:
                    ltm = ytd
                else:
                    fy_prev = _ytd(lk, tag, Quarter(q.year - 1, 4))
                    ytd_prev = _ytd(lk, tag, q.shift(-4))
                    ltm = (
                        ytd + _signed(fy_prev.value, var) - _signed(ytd_prev.value, var)
                        if fy_prev and ytd_prev
                        else math.nan
                    )
                rows.append(
                    {
                        **row,
                        "value": ytd,
                        "quarter": quarter,
                        "ltm": ltm,
                        "concept": tag,
                        "filing": fact.filing,
                        "restated": _restated(fact),
                    }
                )
    cols = ["issuer", "period", "period_end", "variable", "kind", "value", "quarter", "ltm", "concept", "filing"]
    out = pd.DataFrame(rows).reindex(columns=[*cols, "restated"])
    out["restated"] = out["restated"].astype("boolean").fillna(False).astype(bool)
    return out


def restatements(latest: pd.DataFrame, variables: dict[str, Variable]) -> pd.DataFrame:
    """Mapped concepts whose most recent filed value differs from the first-reported one."""
    tags = {t for v in variables.values() for t in v.tags} | {
        t for v in variables.values() for ts in v.overrides.values() for t in ts
    }
    f = latest[latest["concept"].isin(tags) & latest["first_value"].notna()].copy()
    base = f["first_value"].abs().clip(lower=1.0)
    f["change_pct"] = (f["value"] - f["first_value"]) / base
    f = f[f["change_pct"].abs() > RESTATEMENT_TOLERANCE]
    cols = ["issuer", "concept", "start", "end", "first_value", "first_filing", "value", "filing", "change_pct"]
    return f[cols].sort_values(["issuer", "end", "concept"]).reset_index(drop=True)
