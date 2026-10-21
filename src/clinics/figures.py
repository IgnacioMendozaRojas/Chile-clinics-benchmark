"""Figures for the README, regenerated from data/processed on every update.

    python -m clinics.figures --data data/processed --out figures

Conventions: as-reported series; a period a later filing restated gets a hollow ring, and the
indicator recomputed with the revised figures is drawn as a separate diamond (balances in any
quarter; flows only in Q4, where LTM equals the restated full year). Integrated groups (Isapre
+ clinics) are dashed. Vertical lines mark the Supreme Court Isapre ruling (2022Q4) and the
Isapre short law, Ley 21.674 (2024Q2).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from .fetch import Quarter  # noqa: E402
from .issuers import load_issuers  # noqa: E402

# Validated categorical palette (light surface), fixed order by issuer; see the dataviz method.
COLORS = {"clc": "#2a78d6", "indisa": "#eb6834", "redsalud": "#1baf7a", "banmedica": "#eda100", "bupa": "#e87ba4"}
SURFACE, INK, INK_2, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
# quarter: (label, side of the line the label sits on)
EVENTS = {"2022Q4": ("Supreme Court Isapre ruling", "left"), "2024Q2": ("Isapre short law (Ley 21.674)", "right")}
START = "2016Q1"
MATERIAL = 0.05  # revised point shown when the restatement moves the indicator by more than 5%

FIGURES = {
    # name: (indicator, title, subtitle, unit, revisable[, footnote])
    "receivables_over_180": (
        "receivables_over_180_share",
        "Receivables aged more than 180 days",
        "Share of gross trade receivables, CMF portfolio stratification note. Each issuer buckets its "
        "portfolio its own way: read trends, not levels.",
        "pct",
        False,
        "CLC reports only three or four buckets and added one in 2023Q1, so its series is not strictly "
        "comparable across that quarter.",
    ),
    "allowance_coverage": (
        "allowance_coverage",
        "Allowance for credit losses",
        "Allowance as a share of trade receivables before allowance",
        "pct",
        False,
    ),
    "dso": (
        "dso",
        "Days sales outstanding",
        "Trade and other receivables / LTM revenue x 365",
        "days",
        True,
    ),
    "payables_overdue": (
        "payables_overdue_share",
        "Overdue trade payables",
        "Share of trade payables past their due date",
        "pct",
        False,
    ),
    "current_ratio": (
        "current_ratio",
        "Liquidity: current ratio",
        "Current assets / current liabilities",
        "x",
        True,
    ),
    "ebitda_margin": (
        "ebitda_margin",
        "EBITDA margin, last twelve months",
        "(Gross profit - administrative expenses - credit losses + D&A) / revenue",
        "pct",
        True,
    ),
}


def _order(period: str) -> int:
    q = Quarter.parse(period)
    return q.year * 4 + q.q - 1


def revised_indicators(statements: pd.DataFrame) -> pd.DataFrame:
    """Indicators recomputed with restated figures, where a later filing restated a component.

    Balances use the revised figure in any quarter; flows only in Q4 (LTM = the restated year).
    Unrevised components keep their as-reported value.
    """
    s = statements.copy()
    q4 = s["period"].str.endswith("Q4")
    base = np.where(s["kind"] == "flow", s["ltm"], s["value"])
    usable = s["restated"] & s["revised"].notna() & ((s["kind"] == "stock") | q4)
    s["asrep"] = base
    s["rev"] = np.where(usable, s["revised"], base)
    s["hit"] = usable
    a = s.pivot_table(index=["issuer", "period"], columns="variable", values="asrep", aggfunc="first")
    r = s.pivot_table(index=["issuer", "period"], columns="variable", values="rev", aggfunc="first")
    hit = s.pivot_table(index=["issuer", "period"], columns="variable", values="hit", aggfunc="max").fillna(False)

    def col(frame, name, zero=False):
        c = frame[name] if name in frame else pd.Series(np.nan, index=frame.index)
        return c.fillna(0.0) if zero else c

    def ebitda(f):
        return (
            col(f, "gross_profit")
            - col(f, "distribution_costs", True)
            - col(f, "administrative_expense")
            - col(f, "impairment_credit_losses", True)
            + col(f, "depreciation_amortisation")
        )

    formulas = {
        "dso": (
            lambda f: col(f, "trade_receivables_current") / col(f, "revenue") * 365,
            ["trade_receivables_current", "revenue"],
        ),
        "current_ratio": (
            lambda f: col(f, "current_assets") / col(f, "current_liabilities"),
            ["current_assets", "current_liabilities"],
        ),
        "ebitda_margin": (
            lambda f: ebitda(f) / col(f, "revenue"),
            ["gross_profit", "administrative_expense", "depreciation_amortisation", "revenue"],
        ),
    }
    out = []
    for name, (fn, parts) in formulas.items():
        touched = hit.reindex(columns=parts).fillna(False).any(axis=1)
        before, after = fn(a)[touched], fn(r)[touched]
        changed = (after - before).abs() > MATERIAL * before.abs()
        rows = pd.DataFrame({"as_reported": before[changed], "revised": after[changed]}).reset_index()
        out.append(rows.assign(indicator=name))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def _style(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.tick_params(colors=INK_2, labelsize=9, length=0)


def plot(indicators: pd.DataFrame, revised: pd.DataFrame, name: str, out: Path, issuers) -> Path:
    indicator, title, subtitle, unit, revisable, *rest = FIGURES[name]
    footnote = rest[0] if rest else ""
    data = indicators[(indicators["indicator"] == indicator) & (indicators["period"] >= START)]
    fig, ax = plt.subplots(figsize=(9, 4.8), dpi=150)
    fig.patch.set_facecolor(SURFACE)
    _style(ax)
    scale = 100 if unit == "pct" else 1
    ends = []
    for key, iss in issuers.items():
        d = data[data["issuer"] == key].sort_values("period")
        if d.empty:
            continue
        x, y = d["period"].map(_order), d["value"] * scale
        dashed = iss.type == "integrated"
        label = iss.short + (" (integrated)" if dashed else "")
        ax.plot(x, y, color=COLORS[key], linewidth=2, linestyle=(0, (4, 2)) if dashed else "-", label=label)
        if d["period"].iloc[-1] == data["period"].max():  # series that stop earlier: legend only
            ends.append([x.iloc[-1], y.iloc[-1], iss.short])
        if revisable and not revised.empty:
            rv = revised[(revised["indicator"] == indicator) & (revised["issuer"] == key)]
            for r in rv.itertuples(index=False):
                xo = _order(r.period)
                ax.scatter(
                    [xo],
                    [r.as_reported * scale],
                    s=70,
                    facecolors=SURFACE,
                    edgecolors=COLORS[key],
                    linewidths=1.8,
                    zorder=4,
                )
                ax.scatter(
                    [xo],
                    [r.revised * scale],
                    s=55,
                    marker="D",
                    color=COLORS[key],
                    edgecolors=SURFACE,
                    linewidths=1.5,
                    zorder=5,
                )
                ax.plot(
                    [xo, xo],
                    [r.as_reported * scale, r.revised * scale],
                    color=COLORS[key],
                    linewidth=1,
                    linestyle=":",
                    zorder=3,
                )
    lo, hi = ax.get_ylim()
    for period, (text, side) in EVENTS.items():
        xe = _order(period)
        ax.axvline(xe, color=INK_2, linewidth=1, linestyle=":", zorder=1)
        ax.text(
            xe + (-0.4 if side == "left" else 0.4),
            1.01,
            text,
            color=INK_2,
            fontsize=7.5,
            va="bottom",
            ha="right" if side == "left" else "left",
            transform=ax.get_xaxis_transform(),
        )
    # direct labels at the line ends, nudged apart
    ends.sort(key=lambda e: e[1])
    min_gap = (hi - lo) * 0.06
    for i in range(1, len(ends)):
        if ends[i][1] - ends[i - 1][1] < min_gap:
            ends[i][1] = ends[i - 1][1] + min_gap
    for xe, ye, text in ends:
        ax.text(xe + 0.6, ye, text, color=INK, fontsize=8.5, va="center")
    years = range(int(START[:4]), max(int(p[:4]) for p in data["period"]) + 1)
    ax.set_xticks([y * 4 for y in years], [str(y) for y in years])
    first = _order(START)
    last = max(data["period"].map(_order))
    ax.set_xlim(first - 1, last + 7)
    fmt = {"pct": "{:.0f}%", "days": "{:.0f}", "x": "{:.1f}x"}[unit]
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: fmt.format(v)))
    fig.text(0.07, 0.95, title, fontsize=13, color=INK, weight="bold", ha="left")
    fig.text(0.07, 0.905, subtitle, fontsize=8.5, color=INK_2, ha="left", wrap=True)
    handles, labels = ax.get_legend_handles_labels()
    has_revised = revisable and not revised.empty and (revised["indicator"] == indicator).any()
    if has_revised:
        handles += [
            plt.Line2D([], [], marker="o", linestyle="", markerfacecolor=SURFACE, markeredgecolor=INK_2, markersize=7),
            plt.Line2D([], [], marker="D", linestyle="", color=INK_2, markersize=6),
        ]
        labels += ["as reported, later restated", "restated figure"]
    ax.legend(
        handles,
        labels,
        loc="upper left",
        bbox_to_anchor=(0, -0.08),
        ncol=4,
        frameon=False,
        fontsize=8,
        labelcolor=INK_2,
    )
    source = "Source: CMF XBRL filings, as reported. github.com/IgnacioMendozaRojas/chile-clinics-benchmark"
    fig.text(0.07, 0.01, (footnote + "\n" if footnote else "") + source, fontsize=7, color=INK_2, ha="left")
    fig.subplots_adjust(left=0.07, right=0.97, top=0.82, bottom=0.22 if footnote else 0.2)
    path = out / f"{name}.png"
    fig.savefig(path, facecolor=SURFACE, metadata={"Software": None})
    plt.close(fig)
    return path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data", type=Path, default=Path("data/processed"))
    p.add_argument("--out", type=Path, default=Path("figures"))
    args = p.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    indicators = pd.read_csv(args.data / "indicators.csv")
    statements = pd.read_csv(args.data / "statements.csv")
    revised = revised_indicators(statements) if "revised" in statements else pd.DataFrame()
    issuers = load_issuers()
    for name in FIGURES:
        print(plot(indicators, revised, name, args.out, issuers))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
