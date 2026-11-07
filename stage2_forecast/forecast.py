"""
Stage 2: three forecast methods, built only from what was known on the forecast date.

  1. Rep call          won so far + commit + half of best case (how most teams do it)
  2. Stage-weighted    won so far + amount x stage probability from stage_rules.yaml
  3. Historical        won so far + amount x the share of each stage that really closed
                       in earlier quarters, + the average business that earlier quarters
                       won from deals not yet in the pipeline on that week

Each method can run on the raw pipeline or on the clean pipeline (Stage 1 rules applied).
"""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "stage1_hygiene"))
from common import outcomes, quarter_bounds, rules, snapshot_on_week, snapshots  # noqa: E402
from rules_check import apply_fixes, audit  # noqa: E402

METHODS = ["rep_call", "stage_weighted", "historical"]
LEAKAGE_LOG: list[dict] = []   # every data access is recorded and checked in backtest.py


@lru_cache(maxsize=None)
def pipeline(as_of: pd.Timestamp, clean: bool) -> pd.DataFrame:
    snaps = snapshots()
    LEAKAGE_LOG.append({"forecast_date": as_of, "max_snapshot_used": snaps[snaps.snapshot_date <= as_of].snapshot_date.max(),
                        "source": "snapshots"})
    if clean:
        a = audit(snaps[snaps.snapshot_date <= as_of], as_of)
        return apply_fixes(a, None)
    p = snaps[snaps.snapshot_date == as_of].copy()
    p["in_forecast"] = True
    return p


def in_quarter(p: pd.DataFrame, q: pd.Period) -> pd.DataFrame:
    d = p[p.in_forecast & p.amount.notna() & p.close_date.notna()]
    return d[d.close_date.dt.to_period("Q") == q]


def won_so_far(q: pd.Period, as_of: pd.Timestamp) -> float:
    o = outcomes()
    start, _ = quarter_bounds(q)
    w = o[(o.outcome == "won") & (o.closed_date >= start) & (o.closed_date < as_of)]
    LEAKAGE_LOG.append({"forecast_date": as_of, "max_outcome_used": w.closed_date.max(), "source": "outcomes"})
    return float(w.amount_won.sum())


def actual(q: pd.Period) -> float:
    o = outcomes()
    return float(o[(o.outcome == "won") & (o.closed_date.dt.to_period("Q") == q)].amount_won.sum())


@lru_cache(maxsize=None)
def learn_conversion(q: pd.Period, week: int, clean: bool) -> tuple[dict, float, list[str]]:
    """Learn stage conversion and new-business run-rate from full quarters that ended before q started."""
    snaps, o = snapshots(), outcomes()
    first_q = snaps.snapshot_date.min().to_period("Q")
    train = [t for t in pd.period_range(first_q, q - 1, freq="Q")]
    won = o[o.outcome == "won"].set_index("deal_id")
    open_amt: dict[str, float] = {}
    won_amt: dict[str, float] = {}
    runrate = []
    for t in train:
        d = snapshot_on_week(snaps, t, week)
        LEAKAGE_LOG.append({"forecast_date": quarter_bounds(q)[0], "training_quarter_end": quarter_bounds(t)[1],
                            "source": "training"})
        p = in_quarter(pipeline(d, clean), t)
        t_start, t_end = quarter_bounds(t)
        won_t = won[(won.closed_date >= t_start) & (won.closed_date <= t_end)]
        for st, g in p.groupby("stage"):
            open_amt[st] = open_amt.get(st, 0) + g.amount.sum()
            w = won_t.reindex(g.deal_id).amount_won.fillna(0).sum()
            won_amt[st] = won_amt.get(st, 0) + w
        # business won in t that was not in the in-quarter pipeline on that week (and not already won)
        known = set(p.deal_id)
        late = won_t[(~won_t.index.isin(known)) & (won_t.closed_date >= d)]
        runrate.append(float(late.amount_won.sum()))
    conv = {st: (won_amt.get(st, 0) / open_amt[st]) if open_amt.get(st) else rules()["prob"][st]
            for st in rules()["prob"]}
    return conv, (sum(runrate) / len(runrate)) if runrate else 0.0, [str(t) for t in train]


def forecast(q: pd.Period, as_of: pd.Timestamp, week: int, clean: bool) -> dict:
    r = rules()
    p = in_quarter(pipeline(as_of, clean), q)
    base = won_so_far(q, as_of)
    commit = p[p.forecast_category == "commit"].amount.sum()
    best = p[p.forecast_category == "best_case"].amount.sum()
    share = r["rep_call"]["best_case_share"]
    conv, runrate, _ = learn_conversion(q, week, clean)
    return {
        "won_so_far": base,
        "commit": float(commit),
        "best_case": float(best),
        "open_in_quarter": float(p.amount.sum()),
        "rep_call": base + commit + share * best,
        "stage_weighted": base + float((p.amount * p.stage.map(r["prob"])).sum()),
        "historical": base + float((p.amount * p.stage.map(conv)).sum()) + runrate,
    }
