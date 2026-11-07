"""
Stage 2: back-test. Run this file.

  python stage2_forecast/backtest.py

For each of the last 4 completed quarters, it forecasts the quarter at every
week using only the snapshot available on that date, then compares with what
actually closed. Every method runs twice: on the raw pipeline and on the clean
pipeline (Stage 1 rule fixes applied automatically, because nobody can approve
fixes in the past).

Scored weeks: 2, 6 and 10 of each quarter (12 forecasts per method per view).
Error = (forecast - actual) / actual. Absolute error is the accuracy measure;
the signed error shows bias (positive = over-forecast).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "stage2_forecast"))
from common import DATA, OUT, TODAY, outcomes, quarter_bounds, quarter_of, snapshot_on_week, snapshots  # noqa: E402
import forecast as F  # noqa: E402

SCORED_WEEKS = [2, 6, 10]
LABEL = {"rep_call": "Rep call", "stage_weighted": "Stage-weighted", "historical": "Historical conversion"}
INK, MUTED, CLEAN, RAW = "#0b0b0b", "#52514e", "#2a78d6", "#a3a29c"


def backtest_quarters():
    cur = quarter_of(TODAY)
    return [cur - k for k in range(4, 0, -1)]


def run_backtest() -> pd.DataFrame:
    snaps = snapshots()
    rows = []
    for q in backtest_quarters():
        act = F.actual(q)
        n_weeks = len([d for d in snaps.snapshot_date.unique() if quarter_bounds(q)[0] <= d <= quarter_bounds(q)[1]])
        for week in range(1, n_weeks + 1):
            d = snapshot_on_week(snaps, q, week)
            for clean in (False, True):
                f = F.forecast(q, d, week, clean)
                for m in F.METHODS:
                    rows.append(dict(quarter=str(q), week=week, forecast_date=d.date(), data="clean" if clean else "raw",
                                     method=m, forecast=round(f[m]), actual=act,
                                     error_pct=round(100 * (f[m] - act) / act, 1),
                                     abs_error_pct=round(100 * abs(f[m] - act) / act, 1)))
    return pd.DataFrame(rows)


def leakage_check() -> dict:
    """Prove no forecast used information from after its forecast date."""
    bad = []
    for e in F.LEAKAGE_LOG:
        fd = pd.Timestamp(e["forecast_date"])
        if e["source"] == "snapshots" and pd.notna(e["max_snapshot_used"]) and e["max_snapshot_used"] > fd:
            bad.append(e)
        if e["source"] == "outcomes" and pd.notna(e["max_outcome_used"]) and e["max_outcome_used"] >= fd:
            bad.append(e)
        if e["source"] == "training" and pd.Timestamp(e["training_quarter_end"]) >= fd:
            bad.append(e)
    return {"checks_run": len(F.LEAKAGE_LOG), "violations": len(bad), "passed": len(bad) == 0}


def accuracy_tables(bt: pd.DataFrame):
    s = bt[bt.week.isin(SCORED_WEEKS)]
    overall = (s.groupby(["method", "data"])
               .agg(mean_abs_error_pct=("abs_error_pct", "mean"), mean_bias_pct=("error_pct", "mean"),
                    worst_abs_error_pct=("abs_error_pct", "max"), forecasts=("forecast", "size"))
               .round(1).reset_index())
    by_week = (s.groupby(["method", "data", "week"]).agg(mean_abs_error_pct=("abs_error_pct", "mean"),
                                                         mean_bias_pct=("error_pct", "mean")).round(1).reset_index())
    return overall, by_week


def rep_bias() -> pd.DataFrame:
    """Per rep, across weeks 2, 6 and 10 of the four back-test quarters:
    - commit conversion: of the deals they called commit for the quarter, what share of the amount closed won
    - hidden wins: of what they won, what share sat in Proposal or Negotiation but was still categorised pipeline
    """
    snaps, o = snapshots(), outcomes()
    won = o[o.outcome == "won"].set_index("deal_id")
    rows = []
    for q in backtest_quarters():
        start, end = quarter_bounds(q)
        wq = won[(won.closed_date >= start) & (won.closed_date <= end)]
        seen = {}
        for wk in SCORED_WEEKS:
            p = F.in_quarter(F.pipeline(snapshot_on_week(snaps, q, wk), False), q)
            for _, x in p.iterrows():
                s = seen.setdefault(x.deal_id, dict(owner=x.owner, amount=x.amount, commit=False, hidden=False))
                s["commit"] |= x.forecast_category == "commit"
                s["hidden"] |= (x.forecast_category == "pipeline") and x.stage in ("Proposal", "Negotiation")
        for did, s in seen.items():
            rows.append(dict(**s, won=float(wq.amount_won.get(did, 0))))
    df = pd.DataFrame(rows)
    out = df.groupby("owner").apply(lambda x: pd.Series({
        "commit_deals": int(x.commit.sum()),
        "commit_called": x[x.commit].amount.sum(),
        "commit_won": x[x.commit].won.sum(),
        "won_total": x.won.sum(),
        "won_hidden_in_pipeline": x[x.hidden].won.sum(),
    }))
    out["commit_conversion_pct"] = (100 * out.commit_won / out.commit_called.where(out.commit_called > 0)).round(0)
    out["won_hidden_in_pipeline_pct"] = (100 * out.won_hidden_in_pipeline / out.won_total.where(out.won_total > 0)).round(0)

    def call(x):
        if x.commit_deals >= 3 and x.commit_conversion_pct < 40:
            return "optimistic: commit rarely closes"
        if x.won_total > 0 and x.won_hidden_in_pipeline_pct >= 50:
            return "sandbagging: late-stage wins hidden in pipeline"
        return "calibrated"
    out["pattern"] = out.apply(call, axis=1)
    reps = pd.read_csv(DATA / "reps.csv").set_index("owner")
    out["planted_behaviour"] = reps.behaviour.reindex(out.index)
    return out.reset_index().sort_values(["pattern", "commit_conversion_pct"])


def chart(bt: pd.DataFrame, path: Path):
    qs = backtest_quarters()
    fig, axes = plt.subplots(1, 4, figsize=(15, 3.9), sharey=False)
    for ax, q in zip(axes, qs):
        g = bt[(bt.quarter == str(q)) & (bt.method == "historical")]
        r, c = g[g.data == "raw"], g[g.data == "clean"]
        act = g.actual.iloc[0]
        ax.plot(r.week, r.forecast / 1000, color=RAW, lw=2, label="Raw pipeline")
        ax.plot(c.week, c.forecast / 1000, color=CLEAN, lw=2, label="Clean pipeline")
        ax.axhline(act / 1000, color=INK, lw=1.2, ls=(0, (4, 3)), label="Actual closed")
        for w in SCORED_WEEKS:
            ax.axvline(w, color="#e6e5e0", lw=1, zorder=0)
        ax.set_title(str(q), fontsize=11, color=INK, loc="left")
        ax.set_xlabel("Week of quarter", fontsize=9, color=MUTED)
        ax.tick_params(labelsize=8, colors=MUTED)
        for s in ["top", "right"]:
            ax.spines[s].set_visible(False)
        ax.set_ylim(0, max(r.forecast.max(), c.forecast.max(), act) / 1000 * 1.15)
    axes[0].set_ylabel("Forecast ($K)", fontsize=9, color=MUTED)
    axes[0].legend(frameon=False, fontsize=8, loc="lower right")
    fig.suptitle("Historical conversion forecast vs actual, raw vs clean pipeline (synthetic data)",
                 fontsize=11, color=INK, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def bar_chart(overall: pd.DataFrame, path: Path):
    fig, ax = plt.subplots(figsize=(7, 3.4))
    x = range(len(F.METHODS))
    raw = [overall[(overall.method == m) & (overall.data == "raw")].mean_abs_error_pct.iloc[0] for m in F.METHODS]
    cln = [overall[(overall.method == m) & (overall.data == "clean")].mean_abs_error_pct.iloc[0] for m in F.METHODS]
    ax.bar([i - 0.19 for i in x], raw, 0.36, color=RAW, label="Raw pipeline")
    ax.bar([i + 0.19 for i in x], cln, 0.36, color=CLEAN, label="Clean pipeline")
    for i, (a, b) in enumerate(zip(raw, cln)):
        ax.text(i - 0.19, a + 0.8, f"{a:.0f}%", ha="center", fontsize=9, color=INK)
        ax.text(i + 0.19, b + 0.8, f"{b:.0f}%", ha="center", fontsize=9, color=INK)
    ax.set_xticks(list(x), [LABEL[m] for m in F.METHODS], fontsize=9, color=INK)
    ax.set_ylabel("Mean absolute error (%)", fontsize=9, color=MUTED)
    ax.tick_params(axis="y", labelsize=8, colors=MUTED)
    for s in ["top", "right"]:
        ax.spines[s].set_visible(False)
    ax.legend(frameon=False, fontsize=8)
    ax.set_title("Forecast error by method, weeks 2, 6 and 10, four quarters (synthetic data)",
                 fontsize=10, color=INK, loc="left")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main():
    out = OUT / "stage2"
    out.mkdir(parents=True, exist_ok=True)
    bt = run_backtest()
    bt.to_csv(out / "backtest_results.csv", index=False)
    overall, by_week = accuracy_tables(bt)
    overall.to_csv(out / "accuracy_table.csv", index=False)
    by_week.to_csv(out / "accuracy_by_week.csv", index=False)
    rb = rep_bias()
    rb.to_csv(out / "rep_bias.csv", index=False)
    chart(bt, out / "forecast_vs_actual.png")
    bar_chart(overall, out / "error_by_method.png")
    leak = leakage_check()

    # headline: best method on clean data vs the same method on raw data
    piv = overall.pivot(index="method", columns="data", values="mean_abs_error_pct")
    best = piv["clean"].idxmin()
    rep_raw = piv.loc["rep_call", "raw"]
    headline = {"best_method": best, "best_clean_error_pct": float(piv.loc[best, "clean"]),
                "same_method_raw_error_pct": float(piv.loc[best, "raw"]),
                "rep_call_raw_error_pct": float(rep_raw),
                "leakage_check": leak}
    # rep pattern check against planted behaviour
    expect = {"optimistic": "optimistic", "sandbagger": "sandbagging", "normal": "calibrated"}
    rb["match"] = rb.apply(lambda x: x.pattern.split(":")[0] == expect[x.planted_behaviour], axis=1)
    headline["rep_pattern_matches"] = f"{int(rb.match.sum())} of {len(rb)} reps"
    (out / "headline.json").write_text(json.dumps(headline, indent=2, default=str))

    # current quarter forecast for Stage 3
    q = quarter_of(TODAY)
    snaps = snapshots()
    week = sorted(d for d in snaps.snapshot_date.unique() if d >= quarter_bounds(q)[0]).index(TODAY) + 1
    cur = {"quarter": str(q), "week": week, "raw": F.forecast(q, TODAY, week, False),
           "clean": F.forecast(q, TODAY, week, True)}
    err = by_week[(by_week.data == "clean")]
    # error band for the recommended method at the nearest scored week
    nearest = min(SCORED_WEEKS, key=lambda w: abs(w - week))
    cur["recommended_method"] = best
    cur["band_pct"] = float(err[(err.method == best) & (err.week == nearest)].mean_abs_error_pct.iloc[0])
    (out / "forecast_current_quarter.json").write_text(json.dumps(cur, indent=2, default=str))

    print(overall.to_string(index=False))
    print("\n", json.dumps(headline, indent=1, default=str))
    print(rb[["owner", "commit_conversion_pct", "won_hidden_in_pipeline_pct", "pattern", "planted_behaviour"]].to_string(index=False))


if __name__ == "__main__":
    main()
