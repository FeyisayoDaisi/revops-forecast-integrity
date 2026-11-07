"""
Stage 1: Pipeline definition and hygiene. Run this file.

  python stage1_hygiene/run_stage1.py                         # replay saved AI verdicts
  python stage1_hygiene/run_stage1.py --ai live               # call Claude (needs ANTHROPIC_API_KEY)
  python stage1_hygiene/run_stage1.py --approvals stage1_hygiene/approvals.csv   # your own decisions

What happens:
  1. Rules check on today's snapshot (2 Nov 2026): flags, health score, one proposed fix per deal.
  2. AI verdict and owner nudge for each deal with a process flag.
  3. Approval gate: a fix is applied only if approvals.csv says "approve" for that deal.
  4. Writes the clean pipeline, the flagged-deals report and the data-health scorecard.
  5. Also runs the rules + AI step at four earlier audit dates so eval/eval_stage1.py can score them.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "stage1_hygiene"))
sys.path.insert(0, str(ROOT / "eval"))
from common import OUT, TODAY, answer_key, quarter_of, rules, snapshots  # noqa: E402
from rules_check import PROCESS_FLAGS, apply_fixes, audit  # noqa: E402
import ai_verdict  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA_FIXES = {"merge_duplicate", "exclude_missing"}


def needs_ai(a: pd.DataFrame) -> pd.Series:
    return a.flagged & a[PROCESS_FLAGS].any(axis=1) & ~a.fix_type.isin(DATA_FIXES)


def expected_verdict(problems: set) -> str:
    if problems & {"abandoned", "duplicate"}:   # a duplicate the rules missed is a dead record
        return "abandoned"
    if problems & {"mis_staged", "happy_ears"}:
        return "mis_staged"
    if problems & {"serial_slipper", "past_due"}:
        return "stalled"
    return "no_issue"


def weighted_in_quarter(df: pd.DataFrame, q) -> float:
    r = rules()
    d = df[df.get("in_forecast", True) & df.close_date.notna() & df.amount.notna()]
    d = d[d.close_date.dt.to_period("Q") == q]
    return float((d.amount * d.stage.map(r["prob"])).sum())


def scorecard(before: pd.DataFrame, after: pd.DataFrame, q) -> pd.DataFrame:
    def m(df, label):
        a = audit_like(df)
        return {"view": label, "open_deals": len(df),
                "complete_records_pct": round(100 * (df.amount.notna() & df.close_date.notna()).mean(), 1),
                "meeting_exit_criteria_pct": round(100 * (~a.missing_exit_criteria).mean(), 1),
                "stale_pct": round(100 * a.stale.mean(), 1),
                "past_due_pct": round(100 * a.past_due_close_date.mean(), 1),
                "commit_amount": float(df[df.forecast_category == "commit"].amount.sum()),
                f"weighted_pipeline_{q}": round(weighted_in_quarter(df, q))}
    return pd.DataFrame([m(before, "before (raw CRM)"), m(after, "after (approved fixes)")])


def audit_like(df: pd.DataFrame) -> pd.DataFrame:
    """Re-check exit criteria and staleness on a cleaned pipeline (stage may have changed)."""
    r = rules()
    out = pd.DataFrame(index=df.index)
    out["missing_exit_criteria"] = ~df.apply(lambda x: all(
        (x.next_step != "") if f == "next_step" else bool(x[f]) for f in r["by_name"][x.stage]["required_fields"]), axis=1)
    out["stale"] = (df.stage.map(r["order"]) >= r["hygiene"]["stale_min_stage"]) & (df.days_since_activity >= r["hygiene"]["stale_days"])
    pdue = df.close_date.notna() & (df.close_date < df.snapshot_date)
    if "in_forecast" in df:
        pdue &= df.in_forecast
    out["past_due_close_date"] = pdue
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ai", choices=["cache", "live"], default="cache")
    ap.add_argument("--approvals", default=str(HERE / "approvals_demo.csv"))
    ap.add_argument("--export-prompts", action="store_true", help="write all AI prompts to outputs/stage1/ai_prompts.jsonl")
    args = ap.parse_args()

    out = OUT / "stage1"
    out.mkdir(parents=True, exist_ok=True)
    snaps = snapshots()
    r = rules()
    stage_defs = {s["name"]: s["definition"] for s in r["stages"]}
    cache = out / "ai_cache.jsonl"

    # ---- AI step across all audit dates (feeds the evaluation) ----
    from eval_stage1 import audit_dates, truth_at
    ak = answer_key()
    allv, prompts = [], []
    for d in audit_dates(snaps):
        a = audit(snaps, d)
        sel = a[needs_ai(a)]
        prompts.append(sel)
        v = ai_verdict.run(sel, snaps, stage_defs, args.ai, cache)
        truth = truth_at(ak, d)
        v["expected_verdict"] = v.deal_id.map(lambda x: expected_verdict(truth.get(x, set())))
        allv.append(v)
    if args.export_prompts:
        n = ai_verdict.export_prompts(pd.concat(prompts), snaps, stage_defs, out / "ai_prompts.jsonl")
        print(f"wrote {n} prompts to {out / 'ai_prompts.jsonl'}")
    allv = pd.concat(allv, ignore_index=True)
    allv.to_csv(out / "ai_verdicts_all_audits.csv", index=False)

    # ---- today's audit: the deliverable ----
    a = audit(snaps, TODAY)
    today_v = allv[allv.audit_date == str(TODAY.date())].drop(columns=["audit_date", "expected_verdict"])
    a = a.merge(today_v, on="deal_id", how="left")
    a["ai_verdict"] = a.ai_verdict.fillna("")
    a.loc[a.flagged & a.fix_type.isin(DATA_FIXES), "ai_verdict"] = "data fix (no AI needed)"
    a["recommendation"] = a.apply(
        lambda x: "" if not x.flagged else ("reject: AI sees no issue, ask owner" if x.ai_verdict == "no_issue" else "approve"),
        axis=1)
    cols = ["deal_id", "account_name", "owner", "segment", "stage", "amount", "close_date", "forecast_category",
            "health_score", "flag_reasons", "proposed_fix", "ai_verdict", "ai_reason", "owner_nudge", "recommendation"]
    flagged = a[a.flagged][cols]
    flagged.to_csv(out / "flagged_deals.csv", index=False)
    a[["deal_id", "owner", "stage", "health_score", "flag_reasons"]].to_csv(out / "health_scores.csv", index=False)

    # approval template for a real reviewer (never overwritten once it exists)
    tmpl = HERE / "approvals.csv"
    if not tmpl.exists():
        t = flagged[["deal_id", "proposed_fix", "ai_verdict", "recommendation"]].copy()
        t["decision"], t["reviewer"], t["note"] = "", "", ""
        t.to_csv(tmpl, index=False)
    # demo decisions (documented policy: follow the recommendation column)
    demo = flagged[["deal_id", "proposed_fix", "ai_verdict", "recommendation"]].copy()
    demo["decision"] = demo.recommendation.str.split(":").str[0]
    demo["reviewer"] = "demo policy (follows recommendation)"
    demo["note"] = ""
    demo.to_csv(HERE / "approvals_demo.csv", index=False)

    # ---- approval gate ----
    ap_df = pd.read_csv(args.approvals).fillna("")
    approved = set(ap_df[ap_df.decision.str.lower() == "approve"].deal_id)
    clean = apply_fixes(a, approved)
    keep = ["snapshot_date", "deal_id", "account_name", "owner", "segment", "stage", "amount", "close_date",
            "forecast_category", "next_step", "decision_maker_identified", "budget_confirmed", "in_forecast", "fix_applied"]
    clean[keep].to_csv(out / "pipeline_clean.csv", index=False)

    q = quarter_of(TODAY)
    sc = scorecard(a, clean, q)
    sc.to_csv(out / "data_health_scorecard.csv", index=False)

    print(f"Audit {TODAY.date()}: {len(a)} open deals, {int(a.flagged.sum())} flagged, "
          f"{len(approved)} fixes approved via {Path(args.approvals).name}")
    print(sc.to_string(index=False))
    nr = (allv.ai_verdict == "needs_review").sum()
    if nr:
        print(f"note: {nr} AI verdicts are needs_review (no saved response or unreadable output)")


if __name__ == "__main__":
    main()
