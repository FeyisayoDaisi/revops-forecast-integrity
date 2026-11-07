"""Shared helpers: loading the synthetic CRM, dates and quarters."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = ROOT / "outputs"
TODAY = pd.Timestamp("2026-11-02")
FIELDS = ["pain_confirmed", "decision_maker_identified", "budget_confirmed", "paper_process_known"]


@lru_cache(maxsize=1)
def rules() -> dict:
    with open(DATA / "stage_rules.yaml") as f:
        r = yaml.safe_load(f)
    r["by_name"] = {s["name"]: s for s in r["stages"]}
    r["order"] = {s["name"]: s["order"] for s in r["stages"]}
    r["prob"] = {s["name"]: s["win_probability"] for s in r["stages"]}
    return r


@lru_cache(maxsize=1)
def _snapshots() -> pd.DataFrame:
    df = pd.read_csv(DATA / "deals_snapshots.csv", keep_default_na=True)
    for c in ["snapshot_date", "stage_entered_date", "close_date", "created_date", "last_activity_date"]:
        df[c] = pd.to_datetime(df[c], errors="coerce")
    df["next_step"] = df["next_step"].fillna("")
    return df


def snapshots() -> pd.DataFrame:
    return _snapshots().copy()


def outcomes() -> pd.DataFrame:
    o = pd.read_csv(DATA / "deals_outcomes.csv")
    o["closed_date"] = pd.to_datetime(o["closed_date"], errors="coerce")
    return o


def targets() -> pd.DataFrame:
    return pd.read_csv(DATA / "targets.csv")


def answer_key() -> pd.DataFrame:
    a = pd.read_csv(DATA / "answer_key.csv")
    a["true_from"] = pd.to_datetime(a["true_from"])
    return a


def quarter_of(ts) -> pd.Period:
    return pd.Timestamp(ts).to_period("Q")


def quarter_bounds(q: pd.Period) -> tuple[pd.Timestamp, pd.Timestamp]:
    return q.start_time.normalize(), q.end_time.normalize()


def snapshot_on_week(snaps: pd.DataFrame, q: pd.Period, week: int) -> pd.Timestamp:
    """The Monday snapshot that falls in week `week` (1-based) of quarter q."""
    start, _ = quarter_bounds(q)
    dates = sorted(d for d in snaps.snapshot_date.unique() if d >= start)
    return pd.Timestamp(dates[week - 1])


def money(x: float) -> str:
    return f"-${abs(x):,.0f}" if x < 0 else f"${x:,.0f}"
