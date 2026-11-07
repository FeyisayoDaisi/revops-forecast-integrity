"""
Stage 1, step 2: the rules check.

Takes one snapshot date, looks only at CRM data available on that date, and
for every open deal answers: does this record meet our own stage definitions?
Each failed rule becomes a flag, each flag costs health points, and each
flagged deal gets one proposed fix. Nothing is changed here; fixes are only
proposed. A person approves them in approvals.csv (see run_stage1.py).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import FIELDS, rules  # noqa: E402

# the flags that count as "this deal is flagged" (evaluated against the answer key)
FLAGS = ["missing_exit_criteria", "stale", "past_due_close_date", "serial_slipper",
         "happy_ears", "missing_amount_or_close_date", "possible_duplicate"]
PROCESS_FLAGS = ["missing_exit_criteria", "stale", "past_due_close_date", "serial_slipper", "happy_ears"]


def push_counts(snaps: pd.DataFrame, as_of: pd.Timestamp) -> pd.Series:
    """How many times each deal's close date was moved later, using history up to as_of."""
    h = snaps[snaps.snapshot_date <= as_of].sort_values(["deal_id", "snapshot_date"])
    prev = h.groupby("deal_id").close_date.shift()
    pushed = (h.close_date > prev) & prev.notna()
    return pushed.groupby(h.deal_id).sum().astype(int)


def highest_valid_stage(row: pd.Series) -> str:
    """The furthest stage whose exit criteria this record actually meets."""
    r = rules()
    best = r["stages"][0]["name"]
    for st in r["stages"]:
        ok = all((row["next_step"] != "") if f == "next_step" else bool(row[f]) for f in st["required_fields"])
        if ok:
            best = st["name"]
        else:
            break
    return best


def audit(snaps: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    r = rules()
    hyg = r["hygiene"]
    cur = snaps[snaps.snapshot_date == as_of].copy()
    cur["stage_order"] = cur.stage.map(r["order"])
    cur["days_in_stage"] = (as_of - cur.stage_entered_date).dt.days
    cur["days_since_activity"] = (as_of - cur.last_activity_date).dt.days
    cur["close_date_pushes"] = cur.deal_id.map(push_counts(snaps, as_of)).fillna(0).astype(int)

    def meets_exit(row):
        req = r["by_name"][row.stage]["required_fields"]
        return all((row.next_step != "") if f == "next_step" else bool(row[f]) for f in req)

    cur["missing_exit_criteria"] = ~cur.apply(meets_exit, axis=1)
    cur["stale"] = (cur.stage_order >= hyg["stale_min_stage"]) & (cur.days_since_activity >= hyg["stale_days"])
    cur["past_due_close_date"] = cur.close_date.notna() & (cur.close_date < as_of)
    cur["serial_slipper"] = cur.close_date_pushes >= hyg["max_close_date_pushes"]
    cur["happy_ears"] = (cur.forecast_category == "commit") & (
        (cur.next_step == "") | (~cur.decision_maker_identified.astype(bool)))
    cur["over_days_in_stage"] = cur.days_in_stage > cur.stage.map(lambda s: r["by_name"][s]["max_days_in_stage"])
    cur["missing_amount_or_close_date"] = cur.amount.isna() | cur.close_date.isna()

    # duplicates: same account and same amount as an older record anywhere in CRM history
    hist = snaps[snaps.snapshot_date <= as_of].drop_duplicates("deal_id")[["deal_id", "account_name", "amount", "created_date"]]
    first = (hist.dropna(subset=["amount"]).sort_values(["created_date", "deal_id"])
             .drop_duplicates(["account_name", "amount"]).set_index(["account_name", "amount"]).deal_id)
    keys = list(zip(cur.account_name, cur.amount))
    cur["duplicate_of"] = [first.get(k, did) if pd.notna(k[1]) else did for k, did in zip(keys, cur.deal_id)]
    cur.loc[cur.duplicate_of == cur.deal_id, "duplicate_of"] = ""
    cur["possible_duplicate"] = cur.duplicate_of != ""

    pen = r["health_score_penalties"]
    score = pd.Series(100, index=cur.index)
    for flag in FLAGS + ["over_days_in_stage"]:
        score -= cur[flag].astype(int) * pen[flag]
    cur["health_score"] = score.clip(lower=0)
    cur["flagged"] = cur[FLAGS].any(axis=1)
    cur["flag_reasons"] = cur.apply(lambda x: ", ".join(f for f in FLAGS + ["over_days_in_stage"] if x[f]), axis=1)
    cur["valid_stage"] = cur.apply(highest_valid_stage, axis=1)
    fx = cur.apply(propose_fix, axis=1, result_type="expand")
    cur["fix_type"], cur["proposed_fix"] = fx[0], fx[1]
    return cur.sort_values(["health_score", "deal_id"]).reset_index(drop=True)


def propose_fix(row) -> tuple[str, str]:
    """One fix per deal, most important first. Plain words, so an owner can act on it."""
    if row.possible_duplicate:
        return "merge_duplicate", f"Merge into {row.duplicate_of} and close this duplicate"
    if row.stale:
        return "close_lost", f"Close as lost: no buyer activity for {row.days_since_activity} days (owner can object)"
    if row.missing_amount_or_close_date:
        return "exclude_missing", "Keep out of the forecast until amount and close date are filled in"
    if row.happy_ears:
        return "downgrade_commit", "Downgrade from commit to best case until next step and decision maker are recorded"
    if row.missing_exit_criteria and row.valid_stage != row.stage:
        return "restage", f"Move back from {row.stage} to {row.valid_stage} (exit criteria not met)"
    if row.serial_slipper:
        return "move_next_quarter", f"Move to next quarter: close date pushed {row.close_date_pushes} times"
    if row.past_due_close_date:
        return "exclude_past_due", "Keep out of this quarter until the owner sets a real close date"
    if row.missing_exit_criteria:
        return "record_criteria", "Owner to record the missing exit criteria this week"
    return "", ""


def apply_fixes(cur: pd.DataFrame, approved_ids: set[str] | None = None) -> pd.DataFrame:
    """
    Apply each deal's proposed fix to a snapshot and return the clean pipeline.
    approved_ids=None applies every proposed fix. That is only used in the
    Stage 2 back-test, because nobody can approve a fix in the past.
    """
    r = rules()
    out = cur.copy()
    out["in_forecast"] = True
    todo = out.fix_type != ""
    if approved_ids is not None:
        todo &= out.deal_id.isin(approved_ids)
    t = out.fix_type.where(todo, "")
    out.loc[t.isin(["exclude_missing", "exclude_past_due"]), "in_forecast"] = False
    rs = t == "restage"
    out.loc[rs, "stage"] = out.loc[rs, "valid_stage"]
    order = out.stage.map(r["order"])
    out.loc[rs & (order < 4), "forecast_category"] = "pipeline"
    out.loc[rs & (order == 4), "forecast_category"] = "best_case"
    out.loc[(t == "downgrade_commit") & (out.forecast_category == "commit"), "forecast_category"] = "best_case"
    mv = t == "move_next_quarter"
    out.loc[mv, "close_date"] = out.loc[mv, "close_date"].apply(
        lambda d: (d.to_period("Q") + 1).start_time + pd.Timedelta(days=14) if pd.notna(d) else d)
    out["fix_applied"] = t != ""
    return out[~t.isin(["merge_duplicate", "close_lost"])].copy()
