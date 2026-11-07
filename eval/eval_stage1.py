"""
Stage 1 evaluation: score the rules check and the AI verdict step against
the answer key that make_data.py planted.

Audit points: today's snapshot plus week 6 of each of the four back-test
quarters, so the score covers five different pipelines, not one.

For each detector we count, at deal level:
  true positive  = flagged and the planted problem was really there
  false positive = flagged but nothing was planted for that detector
  false negative = a planted problem the detector missed
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "stage1_hygiene"))
from common import OUT, TODAY, answer_key, quarter_of, snapshot_on_week, snapshots  # noqa: E402
from rules_check import FLAGS, audit  # noqa: E402

# which planted problems each detector is meant to catch
DETECTS = {
    "stale": {"abandoned"},
    "missing_exit_criteria": {"mis_staged", "happy_ears"},
    "past_due_close_date": {"past_due", "abandoned", "duplicate"},
    "serial_slipper": {"serial_slipper"},
    "happy_ears": {"happy_ears"},
    "missing_amount_or_close_date": {"missing_data"},
    "possible_duplicate": {"duplicate"},
}
# a planted problem only counts as "missed" by the detector that owns it
OWNER = {"abandoned": "stale", "mis_staged": "missing_exit_criteria", "past_due": "past_due_close_date",
         "serial_slipper": "serial_slipper", "happy_ears": "happy_ears",
         "missing_data": "missing_amount_or_close_date", "duplicate": "possible_duplicate"}


def audit_dates(snaps) -> list[pd.Timestamp]:
    qs = [quarter_of(TODAY) - k for k in range(4, 0, -1)]
    return [snapshot_on_week(snaps, q, 6) for q in qs] + [TODAY]


def truth_at(ak: pd.DataFrame, as_of: pd.Timestamp) -> dict[str, set[str]]:
    live = ak[ak.true_from <= as_of]
    return live.groupby("deal_id").problem.apply(set).to_dict()


def prf(tp, fp, fn, tn=None):
    p = tp / (tp + fp) if tp + fp else float("nan")
    r = tp / (tp + fn) if tp + fn else float("nan")
    fpr = fp / (fp + tn) if tn is not None and (fp + tn) else float("nan")
    return round(p, 3), round(r, 3), round(fpr, 3)


def evaluate_rules():
    snaps, ak = snapshots(), answer_key()
    rows, misses = [], []
    for d in audit_dates(snaps):
        a = audit(snaps, d)
        truth = truth_at(ak, d)
        for _, x in a.iterrows():
            t = truth.get(x.deal_id, set())
            for det in FLAGS:
                owned = {p for p in t if OWNER[p] == det}
                rows.append(dict(audit_date=d.date(), deal_id=x.deal_id, detector=det,
                                 flagged=bool(x[det]), relevant=bool(t & DETECTS[det]), owned=bool(owned)))
                if owned and not x[det]:
                    misses.append(dict(audit_date=str(d.date()), deal_id=x.deal_id, problem=", ".join(sorted(owned)),
                                       detector=det, stage=x.stage, days_since_activity=int(x.days_since_activity),
                                       next_step=x.next_step, flags_raised=x.flag_reasons))
            rows.append(dict(audit_date=d.date(), deal_id=x.deal_id, detector="ANY (deal level)",
                             flagged=bool(x.flagged), relevant=bool(t), owned=bool(t)))
    df = pd.DataFrame(rows)
    res = []
    for det, g in df.groupby("detector", sort=False):
        tp = int((g.flagged & g.relevant).sum())
        fp = int((g.flagged & ~g.relevant).sum())
        fn = int((~g.flagged & g.owned).sum())
        tn = int((~g.flagged & ~g.relevant).sum())
        p, r, fpr = prf(tp, fp, fn, tn)
        res.append(dict(detector=det, true_pos=tp, false_pos=fp, missed=fn, precision=p, recall=r,
                        false_positive_rate=fpr))
    return pd.DataFrame(res), pd.DataFrame(misses).drop_duplicates(["deal_id", "problem"])


def evaluate_ai(verdicts_path: Path):
    """Compare the AI verdicts with the answer key's expected verdict."""
    if not verdicts_path.exists():
        return None, None
    v = pd.read_csv(verdicts_path)
    v = v[v.expected_verdict.notna()]
    labels = ["abandoned", "mis_staged", "stalled", "no_issue"]
    cm = pd.crosstab(v.expected_verdict, v.ai_verdict).reindex(index=labels, columns=labels, fill_value=0)
    acc = (v.expected_verdict == v.ai_verdict).mean()
    per = []
    for lab in labels:
        tp = int(((v.ai_verdict == lab) & (v.expected_verdict == lab)).sum())
        fp = int(((v.ai_verdict == lab) & (v.expected_verdict != lab)).sum())
        fn = int(((v.ai_verdict != lab) & (v.expected_verdict == lab)).sum())
        p, r, _ = prf(tp, fp, fn)
        per.append(dict(verdict=lab, true_pos=tp, false_pos=fp, missed=fn, precision=p, recall=r))
    # rule false alarms the AI correctly cleared
    fa = v[v.expected_verdict == "no_issue"]
    cleared = int((fa.ai_verdict == "no_issue").sum())
    summary = dict(deals_reviewed=int(len(v)), accuracy=round(float(acc), 3),
                   rule_false_alarms=int(len(fa)), false_alarms_cleared_by_ai=cleared)
    wrong = v[v.expected_verdict != v.ai_verdict][["audit_date", "deal_id", "expected_verdict", "ai_verdict", "ai_reason"]]
    return (pd.DataFrame(per), cm, summary), wrong


def main():
    out = OUT / "stage1"
    out.mkdir(parents=True, exist_ok=True)
    res, misses = evaluate_rules()
    res.to_csv(out / "eval_rules.csv", index=False)
    misses.to_csv(out / "eval_rules_misses.csv", index=False)
    print("RULES CHECK (5 audit dates)\n", res.to_string(index=False))
    print("\nmissed planted problems:", len(misses))
    ai, wrong = evaluate_ai(out / "ai_verdicts_all_audits.csv")
    report = {"rules": res.to_dict(orient="records"), "rules_missed": len(misses)}
    if ai:
        per, cm, summary = ai
        per.to_csv(out / "eval_ai.csv", index=False)
        cm.to_csv(out / "eval_ai_confusion.csv")
        wrong.to_csv(out / "eval_ai_disagreements.csv", index=False)
        print("\nAI VERDICT STEP\n", summary, "\n", per.to_string(index=False), "\n", cm)
        report["ai"] = summary
        report["ai_per_verdict"] = per.to_dict(orient="records")
    (out / "eval_summary.json").write_text(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
