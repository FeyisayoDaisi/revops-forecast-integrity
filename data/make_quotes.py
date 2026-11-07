"""
make_quotes.py: generates the synthetic quotes for Stage 4 (deal desk).

One quote per deal that reached Proposal in the synthetic CRM, using the deal's
real amount, owner and outcome from make_data.py. All data is synthetic.

Writes:
  quotes.csv              one row per quote: price, discount, terms, clauses, notes, approvals
  answer_key_quotes.csv   every planted policy break, per quote

Planted breaks:
  missing_approval       a required approver never signed
  over_ceiling           discount above the hard ceiling nobody can approve
  clause_in_notes        a non-standard clause promised in the notes, Legal never saw it
  approved_after_close   the approval is dated after the deal closed
  price_mismatch         list price x discount does not equal the CRM amount
  expired_quote          the deal closed on a quote older than its validity window
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "stage4_deal_desk"))
from policy import policy, required_approvals  # noqa: E402

SEED = 11
DISCOUNTERS = {"Ola Sowande", "Dara Eze"}   # two reps who discount to close
CLEAN_NOTES = ["Champion is the ops lead", "Procurement review booked", "Pricing agreed on the call",
               "Budget approved for this year", "Security questionnaire returned", "Asked for case studies",
               "Customer asked about an SLA, standard SLA applies", "No MFN, customer accepted standard terms"]
CLAUSE_NOTES = {
    "most_favoured_nation": ["Promised MFN pricing to close", "Agreed a best price guarantee"],
    "termination_for_convenience": ["Customer can terminate for convenience after 6 months",
                                    "They can walk away at month 6 if adoption is low"],   # phrased so rules miss it
    "custom_sla": ["Committed to a 99.99% uptime guarantee", "Gave them a 1 hour response time commitment"],
}
ALIASES = {"vp_sales": ["vp_sales", "VP", "VP Sales"], "finance": ["finance", "Finance team"],
           "sales_manager": ["sales_manager", "Manager"], "legal": ["legal", "Legal team"], "cro": ["cro", "CRO"]}


def main():
    rng = np.random.default_rng(SEED)
    s = pd.read_csv(HERE / "deals_snapshots.csv", parse_dates=["snapshot_date"])
    o = pd.read_csv(HERE / "deals_outcomes.csv", parse_dates=["closed_date"]).set_index("deal_id")
    p = policy()
    first = (s[s.stage.isin(["Proposal", "Negotiation"]) & s.amount.notna()]
             .sort_values("snapshot_date").drop_duplicates("deal_id"))
    rows, key = [], []
    for i, x in enumerate(first.itertuples()):
        out = o.loc[x.deal_id]
        closed = out.closed_date if out.outcome in ("won", "lost") else pd.NaT
        qid = f"Q{5000 + i}"
        seg_mu = 9 if x.segment == "SMB" else 14
        d = rng.normal(seg_mu, 6) + (10 if x.owner in DISCOUNTERS else 0)
        if out.outcome == "won" and closed is not pd.NaT and (closed.to_period("Q").end_time - closed).days <= 14:
            d += rng.uniform(5, 10)                    # quarter-end discounting
        d = float(np.clip(round(d, 1), 0, 38))
        q = {
            "quote_id": qid, "deal_id": x.deal_id, "owner": x.owner, "segment": x.segment,
            "net_price": int(x.amount), "discount_pct": d,
            "payment_terms_days": int(rng.choice([30, 45, 60, 90], p=[0.75, 0.1, 0.1, 0.05])),
            "free_months": int(rng.choice([0, 1, 2], p=[0.85, 0.1, 0.05])),
            "term_months": int(rng.choice([12, 24, 36, 18], p=[0.6, 0.2, 0.15, 0.05])),
            "clauses": "", "notes": str(rng.choice(CLEAN_NOTES)),
            "outcome": out.outcome, "closed_date": closed,
        }
        q_date = x.snapshot_date
        if closed is not pd.NaT and (closed - q_date).days > p["quote_valid_days"]:
            q_date = closed - pd.Timedelta(days=int(rng.integers(3, 25)))   # quote revised before signing
        q["quote_date"] = q_date
        if rng.random() < 0.06:
            q["clauses"] = str(rng.choice(p["non_standard_clauses"]))

        # plant breaks (at most one per quote)
        r = rng.random()
        brk = None
        if r < 0.03:
            q["discount_pct"] = float(round(rng.uniform(42, 50), 1)); brk = "over_ceiling"
        elif r < 0.08:
            c = str(rng.choice(list(CLAUSE_NOTES)))
            q["notes"] = str(rng.choice(CLAUSE_NOTES[c])); brk = "clause_in_notes"
        elif r < 0.11:
            brk = "price_mismatch"
        elif r < 0.20 and out.outcome == "won":
            brk = "approved_after_close"
        elif r < 0.29 and out.outcome == "won":
            q["quote_date"] = closed - pd.Timedelta(days=int(rng.integers(45, 90))); brk = "expired_quote"

        need, _ = required_approvals(q)
        if brk == "clause_in_notes":
            need.discard("legal")                     # Legal never saw it
        miss_rate = 0.45 if out.outcome == "open" else 0.12   # the live quarter is where the desk works
        if brk is None and out.outcome == "open" and not need and rng.random() < 0.4:
            q["discount_pct"] = float(round(rng.uniform(21, 29), 1))   # late-stage discount pressure
            need, _ = required_approvals(q)
        if brk is None and need and rng.random() < miss_rate:
            need.discard(sorted(need)[0]); brk = "missing_approval"
        got = []
        for role in sorted(need):
            if role == "sales_manager" and rng.random() < 0.15:
                role = "vp_sales"                     # a more senior approver signed instead (valid)
            got.append(str(rng.choice(ALIASES.get(role, [role]))))
        q["approvals_obtained"] = "; ".join(got)
        base = q["quote_date"] + pd.Timedelta(days=int(rng.integers(1, 4)))
        if brk == "approved_after_close":
            base = closed + pd.Timedelta(days=int(rng.integers(2, 10)))
            if not got:                                # needs an approval to be late
                q["discount_pct"] = max(q["discount_pct"], 15.0)
                q["approvals_obtained"] = "Manager"
        q["approval_date"] = base if q["approvals_obtained"] else pd.NaT
        lp = q["net_price"] / (1 - q["discount_pct"] / 100)
        q["list_price"] = int(round(lp * (rng.uniform(1.04, 1.12) if brk == "price_mismatch" else 1), -1))
        rows.append(q)
        if brk:
            key.append({"quote_id": qid, "deal_id": x.deal_id, "break_type": brk})

    df = pd.DataFrame(rows)
    cols = ["quote_id", "deal_id", "owner", "segment", "quote_date", "list_price", "discount_pct", "net_price",
            "payment_terms_days", "free_months", "term_months", "clauses", "notes", "approvals_obtained",
            "approval_date", "outcome", "closed_date"]
    for c in ["quote_date", "approval_date", "closed_date"]:
        df[c] = pd.to_datetime(df[c]).dt.date
    df[cols].to_csv(HERE / "quotes.csv", index=False)
    pd.DataFrame(key).to_csv(HERE / "answer_key_quotes.csv", index=False)
    print(f"quotes: {len(df)}  planted breaks: {len(key)}  {pd.DataFrame(key).break_type.value_counts().to_dict()}")


if __name__ == "__main__":
    main()
