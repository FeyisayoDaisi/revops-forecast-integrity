"""
Stage 4: Deal desk. Run this file.

  python stage4_deal_desk/deal_desk.py

What it does:
  1. Policy check: every quote against data/deal_desk_policy.yaml. Which approvals did it need,
     which did it get, and what breaks policy outright.
  2. Approval queue: open quotes still waiting on an approver, with who and by when.
  3. Discount analytics: who discounts, whether quarter-end discounting happens, whether
     bigger discounts actually buy more wins, and how much list price was given away.
  4. Forecast link: how much of this quarter's open pipeline sits on a quote that breaks policy.
  5. Evaluation against the planted answer key (data/answer_key_quotes.csv).
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
sys.path.insert(0, str(ROOT / "stage4_deal_desk"))
from common import OUT, TODAY, money  # noqa: E402
from policy import clauses_in_notes, is_covered, normalise_role, policy, required_approvals  # noqa: E402

INK, MUTED, BLUE, GREY, RED = "#0b0b0b", "#52514e", "#2a78d6", "#a3a29c", "#e34948"
BREAKS = ["missing_approval", "over_ceiling", "clause_in_notes", "approved_after_close", "price_mismatch", "expired_quote"]


def load() -> pd.DataFrame:
    q = pd.read_csv(ROOT / "data" / "quotes.csv", parse_dates=["quote_date", "approval_date", "closed_date"])
    for c in ["clauses", "notes", "approvals_obtained"]:
        q[c] = q[c].fillna("")
    return q


def check(q: pd.DataFrame) -> pd.DataFrame:
    p = policy()
    out = []
    for x in q.to_dict("records"):
        need, hard = required_approvals(x)
        got = {normalise_role(a) for a in x["approvals_obtained"].split(";") if a.strip()}
        missing = sorted(r for r in need if not is_covered(r, got))
        found = []
        notes_clause = clauses_in_notes(x["notes"]) - set(filter(None, x["clauses"].split(";")))
        if "legal" in missing and notes_clause:
            found.append("clause_in_notes")
            missing.remove("legal")
        if missing:
            found.append("missing_approval")
        if hard:
            found.append("over_ceiling")
        won = x["outcome"] == "won"
        if won and pd.notna(x["approval_date"]) and x["approval_date"] > x["closed_date"]:
            found.append("approved_after_close")
        expected_net = x["list_price"] * (1 - x["discount_pct"] / 100)
        if abs(expected_net - x["net_price"]) / x["net_price"] > 0.01:
            found.append("price_mismatch")
        if won and (x["closed_date"] - x["quote_date"]).days > p["quote_valid_days"]:
            found.append("expired_quote")
        out.append({**x, "approvals_needed": "; ".join(sorted(need)) or "none (rep can sign)",
                    "approvals_missing": "; ".join(missing), "hard_breaks": "; ".join(hard),
                    "policy_breaks": "; ".join(found), "compliant": not found})
    return pd.DataFrame(out)


def approval_queue(c: pd.DataFrame) -> pd.DataFrame:
    sla = policy()["approval_sla_hours"]
    open_q = c[(c.outcome == "open") & (~c.compliant)].copy()
    rows = []
    for x in open_q.itertuples():
        for role in filter(None, [r.strip() for r in x.approvals_missing.split(";")] +
                                 (["legal"] if "clause_in_notes" in x.policy_breaks else [])):
            rows.append({"quote_id": x.quote_id, "deal_id": x.deal_id, "owner": x.owner, "net_price": x.net_price,
                         "discount_pct": x.discount_pct, "waiting_on": role, "sla_hours": sla.get(role, 24),
                         "why": x.policy_breaks})
        if "over_ceiling" in x.policy_breaks:
            rows.append({"quote_id": x.quote_id, "deal_id": x.deal_id, "owner": x.owner, "net_price": x.net_price,
                         "discount_pct": x.discount_pct, "waiting_on": "rep: rework the quote", "sla_hours": 24,
                         "why": x.hard_breaks})
    return pd.DataFrame(rows).sort_values("net_price", ascending=False) if rows else pd.DataFrame()


def analytics(c: pd.DataFrame) -> dict:
    won = c[c.outcome == "won"].copy()
    closed = c[c.outcome.isin(["won", "lost"])].copy()
    won["quarter_end"] = won.closed_date.apply(lambda d: (d.to_period("Q").end_time - d).days <= 14)
    bands = pd.cut(closed.discount_pct, [-0.1, 10, 20, 30, 100], labels=["0-10%", "10-20%", "20-30%", "30%+"])
    win_by_band = (closed.assign(band=bands).groupby("band", observed=False)
                   .agg(quotes=("quote_id", "size"), win_rate_pct=("outcome", lambda s: round(100 * (s == "won").mean(), 0))))
    by_rep = (won.groupby("owner").agg(won_deals=("quote_id", "size"), avg_discount_pct=("discount_pct", "mean"))
              .round(1).sort_values("avg_discount_pct", ascending=False))
    team_avg = float(won.discount_pct.mean())
    by_rep["vs_team_pts"] = (by_rep.avg_discount_pct - team_avg).round(1)
    # flag only with enough deals to judge: 5+ won deals and 5+ points above the team
    by_rep["flag"] = (by_rep.won_deals >= 5) & (by_rep.vs_team_pts >= 5)
    given_away = float((won.list_price - won.net_price).clip(lower=0).sum())
    unapproved = won[won.policy_breaks.str.contains("missing_approval|over_ceiling|approved_after_close")]
    return {
        "won_deals": int(len(won)),
        "avg_discount_won_pct": round(team_avg, 1),
        "avg_discount_quarter_end_pct": round(float(won[won.quarter_end].discount_pct.mean()), 1),
        "avg_discount_rest_of_quarter_pct": round(float(won[~won.quarter_end].discount_pct.mean()), 1),
        "list_price_given_away": round(given_away),
        "won_revenue": int(won.net_price.sum()),
        "won_deals_with_unapproved_discount": int(len(unapproved)),
        "revenue_on_unapproved_terms": int(unapproved.net_price.sum()),
        "win_rate_by_discount_band": win_by_band.reset_index().astype({"band": str}).to_dict(orient="records"),
        "by_rep": by_rep.reset_index(),
    }


def forecast_link(c: pd.DataFrame) -> dict:
    """Open in-quarter deals (clean pipeline from Stage 1) whose quote breaks policy."""
    path = OUT / "stage1" / "pipeline_clean.csv"
    if not path.exists():
        return {}
    pipe = pd.read_csv(path, parse_dates=["close_date"])
    q = TODAY.to_period("Q")
    pipe = pipe[pipe.in_forecast & (pipe.close_date.dt.to_period("Q") == q)]
    m = pipe.merge(c[["deal_id", "quote_id", "policy_breaks", "compliant"]], on="deal_id", how="inner")
    risky = m[~m.compliant]
    return {"in_quarter_deals_with_quote": int(len(m)), "with_policy_break": int(len(risky)),
            "amount_on_broken_quotes": int(risky.amount.sum()),
            "deals": risky[["deal_id", "quote_id", "owner", "stage", "amount", "forecast_category", "policy_breaks"]]
            .to_dict(orient="records")}


def evaluate(c: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    ak = pd.read_csv(ROOT / "data" / "answer_key_quotes.csv")
    truth = dict(zip(ak.quote_id, ak.break_type))
    rows, misses = [], []
    for b in BREAKS:
        det = c.policy_breaks.str.contains(b)
        tru = c.quote_id.map(truth).eq(b)
        tp, fp, fn = int((det & tru).sum()), int((det & ~tru).sum()), int((~det & tru).sum())
        rows.append({"break_type": b, "planted": int(tru.sum()), "caught": tp, "false_alarms": fp, "missed": fn,
                     "precision": round(tp / (tp + fp), 3) if tp + fp else None,
                     "recall": round(tp / (tp + fn), 3) if tp + fn else None})
        for x in c[~det & tru].itertuples():
            misses.append({"quote_id": x.quote_id, "planted": b, "detected": x.policy_breaks, "notes": x.notes})
        for x in c[det & ~tru].itertuples():
            misses.append({"quote_id": x.quote_id, "planted": truth.get(x.quote_id, "none"), "detected": b, "notes": x.notes})
    any_det, any_tru = ~c.compliant, c.quote_id.isin(truth)
    tp, fp, fn = int((any_det & any_tru).sum()), int((any_det & ~any_tru).sum()), int((~any_det & any_tru).sum())
    rows.append({"break_type": "ANY (quote level)", "planted": int(any_tru.sum()), "caught": tp, "false_alarms": fp,
                 "missed": fn, "precision": round(tp / (tp + fp), 3), "recall": round(tp / (tp + fn), 3)})
    return pd.DataFrame(rows), pd.DataFrame(misses).drop_duplicates()


def chart(a: dict, path: Path):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 3.8))
    wb = pd.DataFrame(a["win_rate_by_discount_band"])
    ax1.bar(wb.band, wb.win_rate_pct, color=BLUE, width=0.55)
    for i, r in wb.iterrows():
        ax1.text(i, r.win_rate_pct + 1.5, f"{r.win_rate_pct:.0f}%\n({r.quotes} quotes)", ha="center", fontsize=8.5, color=INK)
    ax1.set_ylim(0, max(wb.win_rate_pct) * 1.35)
    ax1.set_title("Win rate by discount band: bigger discounts did not buy more wins", fontsize=10, loc="left", color=INK)
    ax1.set_ylabel("Win rate (%)", fontsize=9, color=MUTED)
    r = a["by_rep"].sort_values("avg_discount_pct")
    colors = [RED if f else GREY for f in r.flag]
    ax2.barh(r.owner, r.avg_discount_pct, color=colors, height=0.6)
    ax2.axvline(a["avg_discount_won_pct"], color=INK, lw=1.2, ls=(0, (4, 3)))
    ax2.text(a["avg_discount_won_pct"] + 0.3, -0.9, "team average", fontsize=8, color=MUTED)
    ax2.set_title("Average discount on won deals, by rep", fontsize=10, loc="left", color=INK)
    ax2.set_xlabel("Discount (%)", fontsize=9, color=MUTED)
    for ax in (ax1, ax2):
        ax.tick_params(labelsize=8, colors=MUTED)
        for s in ["top", "right"]:
            ax.spines[s].set_visible(False)
    fig.suptitle("Deal desk (synthetic data)", fontsize=10, color=MUTED, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main():
    out = OUT / "stage4"
    out.mkdir(parents=True, exist_ok=True)
    c = check(load())
    c.to_csv(out / "quote_checks.csv", index=False)
    c[~c.compliant][["quote_id", "deal_id", "owner", "net_price", "discount_pct", "approvals_needed",
                     "approvals_obtained", "policy_breaks", "outcome"]].to_csv(out / "policy_breaks.csv", index=False)
    aq = approval_queue(c)
    aq.to_csv(out / "approval_queue.csv", index=False)
    a = analytics(c)
    a["by_rep"].to_csv(out / "discount_by_rep.csv", index=False)
    fl = forecast_link(c)
    ev, misses = evaluate(c)
    ev.to_csv(out / "eval_policy_check.csv", index=False)
    misses.to_csv(out / "eval_misses.csv", index=False)
    chart(a, out / "deal_desk.png")
    summary = {k: v for k, v in a.items() if k != "by_rep"}
    summary["quotes_checked"] = int(len(c))
    summary["quotes_breaking_policy"] = int((~c.compliant).sum())
    summary["open_quotes_waiting_on_approval"] = int(aq.quote_id.nunique()) if len(aq) else 0
    summary["forecast_link"] = {k: v for k, v in fl.items() if k != "deals"}
    summary["evaluation"] = ev.to_dict(orient="records")
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    pd.DataFrame(fl.get("deals", [])).to_csv(out / "forecast_deals_on_broken_quotes.csv", index=False)

    print(f"{len(c)} quotes checked, {int((~c.compliant).sum())} break policy")
    print(ev.to_string(index=False))
    print(json.dumps({k: summary[k] for k in ["avg_discount_won_pct", "avg_discount_quarter_end_pct",
                                              "avg_discount_rest_of_quarter_pct", "list_price_given_away",
                                              "won_revenue", "won_deals_with_unapproved_discount",
                                              "revenue_on_unapproved_terms", "win_rate_by_discount_band",
                                              "forecast_link", "open_quotes_waiting_on_approval"]}, indent=1, default=str))
    print(a["by_rep"].head(5).to_string(index=False))


if __name__ == "__main__":
    main()
