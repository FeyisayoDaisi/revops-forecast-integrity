"""
make_data.py: generates the synthetic CRM used by every stage of the
Forecast Integrity System.

All data is synthetic. Company names, people and numbers are invented.
The script is seeded, so every run produces exactly the same files.

What it writes (into this folder):
  deals_snapshots.csv  weekly snapshots of the open pipeline (what a CRM export looks like)
  deals_outcomes.csv   final result per deal
  targets.csv          quarterly target per segment
  answer_key.csv       every planted problem, per deal, with the date it became true
  reps.csv             rep roster with their planted calling behaviour (for the Stage 2 check)

How it works, in plain words:
  1. Create about 340 deals with a real (hidden) outcome and a real timeline.
  2. Walk every Monday from April 2025 to 2 November 2026 and record what the
     CRM would show on that day.
  3. Plant the problems revenue teams actually live with (zombie deals,
     mis-staged deals, serial slippers, happy ears, sandbagging, past-due
     dates, missing data, duplicates, optimistic reps) and write each one
     into the answer key, so every later stage can be scored.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 7
HERE = Path(__file__).resolve().parent
SNAP_START = dt.date(2025, 4, 7)      # first Monday of Q2 2025
TODAY = dt.date(2026, 11, 2)          # the "current" snapshot (week 5 of Q4 2026)
CREATE_START = dt.date(2025, 1, 6)
CREATE_END = dt.date(2026, 10, 30)
N_DEALS = 340

STAGES = ["Discovery", "Qualification", "Solution", "Proposal", "Negotiation"]
FIELDS_BY_STAGE = {  # field that becomes true when a deal ENTERS this stage
    1: "next_step",
    2: "pain_confirmed",
    3: "decision_maker_identified",
    4: "budget_confirmed",
    5: "paper_process_known",
}

FIRST = ["Amara", "Bola", "Chidi", "Dara", "Efe", "Femi", "Grace", "Hassan",
         "Ifeoma", "Jide", "Kemi", "Lola", "Musa", "Nneka", "Ola"]
LAST = ["Adeyemi", "Okafor", "Bello", "Eze", "Ogunleye", "Nwosu", "Abubakar",
        "Mensah", "Owusu", "Kamau", "Diallo", "Banda", "Mwangi", "Achebe", "Sowande"]
ACCT_A = ["Northwind", "Bluepeak", "Kestrel", "Harbor", "Lumen", "Cobalt", "Juniper",
          "Atlas", "Meridian", "Saffron", "Granite", "Orbit", "Pioneer", "Cedar",
          "Vertex", "Willow", "Summit", "Ember", "Quarry", "Tidal", "Beacon", "Falcon"]
ACCT_B = ["Logistics", "Health", "Analytics", "Foods", "Freight", "Labs", "Retail",
          "Energy", "Finance", "Systems", "Learning", "Media", "Mobility", "Clinics"]
NEXT_STEPS = ["Demo with ops lead", "Security questionnaire review", "Pricing call",
              "Technical deep dive", "Exec alignment call", "Contract redlines review",
              "Workshop with finance", "Reference call", "Pilot kickoff"]


def mondays(start: dt.date, end: dt.date) -> list[dt.date]:
    out, d = [], start
    while d <= end:
        out.append(d)
        d += dt.timedelta(days=7)
    return out


def quarter_end(d: dt.date) -> dt.date:
    q_end_month = 3 * ((d.month - 1) // 3 + 1)
    nxt = dt.date(d.year + (q_end_month == 12), (q_end_month % 12) + 1, 1)
    return nxt - dt.timedelta(days=1)


def main() -> None:
    rng = np.random.default_rng(SEED)

    # ---------------- reps ----------------
    reps = pd.DataFrame({
        "owner": [f"{FIRST[i]} {LAST[i]}" for i in range(15)],
        "segment": ["SMB"] * 8 + ["Mid-Market"] * 7,
    })
    reps["behaviour"] = "normal"
    reps.loc[[2, 10], "behaviour"] = "optimistic"   # commit too early, happy ears
    reps.loc[[5, 12], "behaviour"] = "sandbagger"   # hide deals in pipeline until they sign
    behaviour = dict(zip(reps.owner, reps.behaviour))

    # ---------------- deals (the hidden truth) ----------------
    span = (CREATE_END - CREATE_START).days
    deals = []
    acct_used: set[str] = set()
    for i in range(N_DEALS):
        seg = "SMB" if rng.random() < 0.58 else "Mid-Market"
        owner = rng.choice(reps.loc[reps.segment == seg, "owner"].to_numpy())
        while True:  # 22 x 14 x 3 = 924 possible names, all unique
            acct = f"{rng.choice(ACCT_A)} {rng.choice(ACCT_B)} {rng.choice(['Ltd', 'Group', 'Co'])}"
            if acct not in acct_used:
                acct_used.add(acct)
                break
        created = CREATE_START + dt.timedelta(days=int(rng.integers(0, span)))
        if seg == "SMB":
            amount = int(round(rng.uniform(8_000, 32_000), -2))
            cycle = int(rng.integers(35, 85))
            p_win = 0.34
        else:
            amount = int(round(rng.uniform(30_000, 150_000), -3))
            cycle = int(rng.integers(60, 140))
            p_win = 0.30
        won = rng.random() < p_win
        if won:
            max_stage = 5
        else:
            max_stage = int(rng.choice([1, 2, 3, 4, 5], p=[0.22, 0.25, 0.22, 0.17, 0.14]))
        deals.append(dict(
            deal_id=f"D{1000 + i}", account_name=acct, owner=owner, segment=seg,
            created=created, amount=amount, cycle=cycle, won=won, max_stage=max_stage,
            behaviour=behaviour[owner], problems=[],
        ))

    # ---------------- plant problems ----------------
    idx = np.arange(N_DEALS)
    rng.shuffle(idx)

    def pick(cond, frac, used=None):
        order = rng.permutation(N_DEALS)  # fresh order per problem, so problems are not correlated
        cands = [j for j in order if cond(deals[j]) and (used is None or j not in used)]
        k = int(round(len(cands) * frac))
        return set(cands[:k])

    zombies = pick(lambda d: not d["won"] and d["max_stage"] >= 3, 0.55)
    misstaged = pick(lambda d: not d["won"] and 2 <= d["max_stage"] <= 4, 0.40, zombies)
    slippers = pick(lambda d: True, 0.14, zombies)
    happy = pick(lambda d: not d["won"] and d["max_stage"] >= 3 and d["behaviour"] == "optimistic", 0.85)
    happy |= pick(lambda d: not d["won"] and d["max_stage"] >= 3 and d["behaviour"] == "normal", 0.06)
    past_due = pick(lambda d: True, 0.06, zombies)
    missing = pick(lambda d: True, 0.03)
    # the current quarter is the one being audited, so it gets a fuller mix of problems
    recent = lambda d: d["created"] >= dt.date(2026, 6, 15)
    used = zombies | misstaged | slippers | happy | past_due
    misstaged |= pick(lambda d: recent(d) and not d["won"] and 2 <= d["max_stage"] <= 4, 0.5, used)
    slippers |= pick(lambda d: recent(d) and d["created"] <= dt.date(2026, 8, 31), 0.25, used | misstaged)
    past_due |= pick(lambda d: recent(d), 0.08, used | misstaged | slippers)
    for j in zombies:
        deals[j]["problems"].append("abandoned")
    for j in misstaged:
        deals[j]["problems"].append("mis_staged")
    for j in slippers:
        deals[j]["problems"].append("serial_slipper")
    for j in happy:
        deals[j]["problems"].append("happy_ears")
    for j in past_due:
        deals[j]["problems"].append("past_due")
    for j in missing:
        deals[j]["problems"].append("missing_data")

    # duplicates: copies of open-looking deals, entered a few days later, never worked
    dup_src = [j for j in rng.permutation(N_DEALS) if deals[j]["max_stage"] >= 2 and not deals[j]["problems"]][:14]
    for k, j in enumerate(dup_src):
        src = deals[j]
        d = dict(src)
        d.update(deal_id=f"D{1000 + N_DEALS + k}",
                 created=src["created"] + dt.timedelta(days=int(rng.integers(2, 10))),
                 won=False, max_stage=min(src["max_stage"], 2), problems=["duplicate"],
                 dup_of=src["deal_id"], cycle=src["cycle"])
        if k % 4 == 3:  # retyped by hand, amount does not match exactly
            d["amount"] = int(src["amount"] * 0.98)
        deals.append(d)

    # ---------------- timelines ----------------
    for d in deals:
        c = d["created"]
        if "serial_slipper" in d["problems"]:
            d["cycle"] = int(d["cycle"] * rng.uniform(1.5, 2.0))
        cyc = d["cycle"]
        end = c + dt.timedelta(days=cyc)                     # real close or death
        fr_won = [0.0, 0.15, 0.35, 0.60, 0.80]
        n = d["max_stage"]
        if d["won"]:
            fr = fr_won
        else:
            fr = [x * (0.9 / max(fr_won[n - 1], 0.15)) if k else 0 for k, x in enumerate(fr_won[:n])]
        d["stage_dates"] = [c + dt.timedelta(days=int(cyc * min(f, 0.95))) for f in fr]
        d["end"] = end
        # CRM record closes a few days after reality, zombies stay open for months
        if "abandoned" in d["problems"]:
            d["crm_close"] = end + dt.timedelta(days=int(rng.integers(80, 260)))
        elif "duplicate" in d["problems"]:
            d["crm_close"] = c + dt.timedelta(days=int(rng.integers(120, 400)))
            d["end"] = c + dt.timedelta(days=int(rng.integers(5, 15)))  # never worked
        else:
            d["crm_close"] = end + dt.timedelta(days=int(rng.integers(0, 6)))
        # activity: every 2 to 12 days while alive; some healthy deals have one long quiet gap
        acts, t = [c], c
        gap_at = None
        if not d["problems"] and rng.random() < 0.22:
            gap_at = c + dt.timedelta(days=int(cyc * rng.uniform(0.5, 0.75)))
        while True:
            step = int(rng.integers(2, 13))
            if gap_at and t >= gap_at:
                step, gap_at = int(rng.integers(46, 58)), None
            t = t + dt.timedelta(days=step)
            if t >= d["end"]:
                break
            acts.append(t)
        d["acts"] = acts
        # rep's expected close date
        optimism = rng.uniform(0.85, 1.05) if "serial_slipper" not in d["problems"] else rng.uniform(0.35, 0.5)
        d["exp_close0"] = c + dt.timedelta(days=int(cyc * optimism))
        # dates for planted behaviours
        d["misstage_from"] = d["stage_dates"][min(1, n - 1)] + dt.timedelta(days=int(rng.integers(3, 15))) \
            if "mis_staged" in d["problems"] else None
        d["ticked_boxes"] = "mis_staged" in d["problems"] and rng.random() < 0.3
        d["vague_next_step"] = "happy_ears" in d["problems"] and rng.random() < 0.25
        d["happy_from"] = d["stage_dates"][2] if "happy_ears" in d["problems"] and n >= 3 else None
        d["pastdue_freeze"] = c + dt.timedelta(days=int(cyc * rng.uniform(0.3, 0.6))) \
            if "past_due" in d["problems"] else None
        d["missing_field"] = rng.choice(["amount", "close_date"]) if "missing_data" in d["problems"] else None

    # ---------------- weekly snapshots ----------------
    snaps = []
    truth_start: dict[tuple[str, str], dt.date] = {}

    def mark(deal_id, prob, when):
        truth_start.setdefault((deal_id, prob), when)

    for d in deals:
        exp_close = d["exp_close0"]
        pushes = 0
        frozen_close = False
        for s in mondays(SNAP_START, TODAY):
            if s < d["created"] or s >= d["crm_close"]:
                continue
            alive = s < d["end"]
            # true stage today
            true_stage = sum(1 for sd in d["stage_dates"] if sd <= s)
            true_stage = max(1, min(true_stage, d["max_stage"]))
            shown = true_stage
            if d["misstage_from"] and s >= d["misstage_from"] and true_stage < 5:
                shown = min(true_stage + 1, 5)
                mark(d["deal_id"], "mis_staged", d["misstage_from"])
            # stage entered date (for days in stage)
            if shown > true_stage:
                entered = d["misstage_from"]
            else:
                entered = d["stage_dates"][shown - 1]
            # fields: true when the deal really reached that stage
            fields = {f: (true_stage >= st) for st, f in FIELDS_BY_STAGE.items()}
            if d["ticked_boxes"] and shown > true_stage:
                fields[FIELDS_BY_STAGE[shown]] = True
            # activity
            last_act = max(a for a in d["acts"] if a <= s)
            if not alive and "abandoned" in d["problems"]:
                mark(d["deal_id"], "abandoned", d["end"])
            if "duplicate" in d["problems"]:
                mark(d["deal_id"], "duplicate", d["created"])
            # close date management
            if d["pastdue_freeze"] and s >= d["pastdue_freeze"]:
                frozen_close = True
            if not alive:
                frozen_close = True      # nobody touches a dead deal
            if not frozen_close and s > exp_close:
                if "serial_slipper" in d["problems"]:   # pushed a week or two at a time
                    exp_close = s + dt.timedelta(days=int(rng.integers(6, 15)))
                else:
                    exp_close = s + dt.timedelta(days=int(rng.integers(14, 36)))
                pushes += 1
                if "serial_slipper" in d["problems"] and pushes == 3:
                    mark(d["deal_id"], "serial_slipper", s)
            if s > exp_close and frozen_close and alive and d["pastdue_freeze"]:
                mark(d["deal_id"], "past_due", exp_close + dt.timedelta(days=1))
            # forecast category
            beh = d["behaviour"]
            cat = "pipeline"
            if shown >= 5:
                cat = "commit"
            elif shown == 4:
                cat = "best_case"
            if beh == "optimistic" and shown >= 3 and quarter_end(exp_close) == quarter_end(s):
                cat = "commit"
            if beh == "sandbagger" and d["won"] and (d["end"] - s).days > 10:
                cat = "pipeline"
            happy_now = d["happy_from"] and s >= d["happy_from"]
            if happy_now:
                cat = "commit"
                fields["decision_maker_identified"] = False
                mark(d["deal_id"], "happy_ears", d["happy_from"])
            # next step text
            ns = ""
            if fields["next_step"]:
                if happy_now:
                    ns = "Follow up" if d["vague_next_step"] else ""
                elif alive:
                    ns = f"{rng.choice(NEXT_STEPS)} on {(s + dt.timedelta(days=int(rng.integers(2, 12)))).isoformat()}"
                else:
                    ns = "Follow up"  # left over text on a dead deal
            fields["next_step"] = ns
            amount = d["amount"]
            close_out = exp_close.isoformat()
            if d["missing_field"] == "amount":
                amount = None
                mark(d["deal_id"], "missing_data", d["created"])
            elif d["missing_field"] == "close_date":
                close_out = ""
                mark(d["deal_id"], "missing_data", d["created"])
            snaps.append({
                "snapshot_date": s.isoformat(), "deal_id": d["deal_id"],
                "account_name": d["account_name"], "owner": d["owner"],
                "segment": d["segment"], "stage": STAGES[shown - 1],
                "stage_entered_date": entered.isoformat(), "amount": amount,
                "close_date": close_out, "forecast_category": cat,
                "created_date": d["created"].isoformat(),
                "last_activity_date": last_act.isoformat(),
                "next_step": fields["next_step"],
                "pain_confirmed": bool(fields["pain_confirmed"]),
                "decision_maker_identified": bool(fields["decision_maker_identified"]),
                "budget_confirmed": bool(fields["budget_confirmed"]),
                "paper_process_known": bool(fields["paper_process_known"]),
            })

    snap_df = pd.DataFrame(snaps).sort_values(["snapshot_date", "deal_id"])
    snap_df["amount"] = snap_df["amount"].astype("Int64")
    snap_df.to_csv(HERE / "deals_snapshots.csv", index=False)

    # ---------------- outcomes ----------------
    out = []
    for d in deals:
        if d["crm_close"] > TODAY:
            out.append((d["deal_id"], "open", "", 0))
        elif d["won"]:
            out.append((d["deal_id"], "won", d["crm_close"].isoformat(), d["amount"]))
        else:
            out.append((d["deal_id"], "lost", d["crm_close"].isoformat(), 0))
    out_df = pd.DataFrame(out, columns=["deal_id", "outcome", "closed_date", "amount_won"])
    out_df.to_csv(HERE / "deals_outcomes.csv", index=False)

    # ---------------- targets ----------------
    won = out_df[out_df.outcome == "won"].merge(
        pd.DataFrame([(d["deal_id"], d["segment"]) for d in deals], columns=["deal_id", "segment"]))
    won["quarter"] = pd.PeriodIndex(pd.to_datetime(won.closed_date), freq="Q").astype(str)
    base = won.groupby("segment").amount_won.sum() / won.quarter.nunique()
    tg = []
    for q in pd.period_range("2025Q2", "2026Q4", freq="Q"):
        for seg in ["SMB", "Mid-Market"]:
            tg.append((str(q), seg, int(round(base[seg] * 1.08, -4))))
    pd.DataFrame(tg, columns=["quarter", "segment", "target"]).to_csv(HERE / "targets.csv", index=False)

    # ---------------- answer key ----------------
    verdict = {"abandoned": "abandoned", "mis_staged": "mis_staged", "happy_ears": "mis_staged",
               "serial_slipper": "stalled", "past_due": "stalled",
               "duplicate": "data_fix", "missing_data": "data_fix"}
    ak = [{"deal_id": k[0], "problem": k[1], "true_from": v.isoformat(),
           "expected_verdict": verdict[k[1]]} for k, v in sorted(truth_start.items())]
    pd.DataFrame(ak).to_csv(HERE / "answer_key.csv", index=False)
    reps[["owner", "segment", "behaviour"]].to_csv(HERE / "reps.csv", index=False)

    print(f"deals: {len(deals)}  snapshot rows: {len(snap_df)}  planted problems: {len(ak)}")
    print(f"open in today's snapshot: {(snap_df.snapshot_date == TODAY.isoformat()).sum()}")


if __name__ == "__main__":
    main()
