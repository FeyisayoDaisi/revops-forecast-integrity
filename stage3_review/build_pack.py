"""
Stage 3: weekly forecast review pack. Run this file (after Stages 1 and 2).

  python stage3_review/build_pack.py             # memo narrative replayed from the saved AI response
  python stage3_review/build_pack.py --ai live   # Claude writes the narrative (needs ANTHROPIC_API_KEY)
  python stage3_review/build_pack.py --ai template   # no AI, plain template narrative

The script computes every number. The AI step only writes the narrative from
those numbers and is never allowed to produce a number of its own: the
reconciliation checks fail the pack if the memo contains any figure that is
not in the computed facts.

Writes outputs/stage3/: review_pack.html, forecast_memo.md, forecast_memo.pdf,
waterfall.csv, at_risk.csv, rep_vs_model.csv, facts.json, reconciliation.csv
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import re
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "stage1_hygiene"))
sys.path.insert(0, str(ROOT / "stage2_forecast"))
from common import OUT, TODAY, money, outcomes, quarter_bounds, quarter_of, rules, snapshots, targets  # noqa: E402
import forecast as F  # noqa: E402

LAST_WEEK = TODAY - pd.Timedelta(days=7)
MODEL = "claude-opus-5-5"


# ------------------------------------------------------------------ the number
def the_number(cur: dict, q) -> dict:
    tg = int(targets().query("quarter == @q_str").target.sum()) if (q_str := str(q)) else 0
    c = cur["clean"]
    rec = c[cur["recommended_method"]]
    band = cur["band_pct"] / 100
    remaining = tg - c["won_so_far"]
    return {
        "quarter": str(q), "week": cur["week"], "target": tg, "won_so_far": round(c["won_so_far"]),
        "commit_clean": round(c["won_so_far"] + c["commit"]),
        "best_case_clean": round(c["won_so_far"] + c["commit"] + c["best_case"]),
        "rep_call_raw": round(cur["raw"]["rep_call"]),
        "recommended_forecast": round(rec, -3),
        "range_low": round(rec * (1 - band), -3), "range_high": round(rec * (1 + band), -3),
        "band_pct": round(cur["band_pct"]),
        "gap_to_target": round(tg - rec, -3),
        "coverage_ratio": round(c["open_in_quarter"] / remaining, 1) if remaining > 0 else None,
        "method": cur["recommended_method"],
    }


# ------------------------------------------------------------------ waterfall
def waterfall(q) -> tuple[pd.DataFrame, dict]:
    """Movement of the in-quarter open pipeline (raw CRM) from last week to this week."""
    s, o = snapshots(), outcomes().set_index("deal_id")
    prev = F.in_quarter(F.pipeline(LAST_WEEK, False), q).set_index("deal_id")
    cur_all = s[s.snapshot_date == TODAY].set_index("deal_id")
    cur = F.in_quarter(F.pipeline(TODAY, False), q).set_index("deal_id")
    order = rules()["order"]
    moves = {"new": 0.0, "pulled_in": 0.0, "pushed_out": 0.0, "won": 0.0, "lost": 0.0, "amount_change": 0.0}
    detail = []
    for did in cur.index.difference(prev.index):
        x = cur.loc[did]
        kind = "new" if x.created_date > LAST_WEEK else "pulled_in"
        moves[kind] += x.amount
        detail.append((did, kind, x.amount))
    for did in prev.index.difference(cur.index):
        x = prev.loc[did]
        if did in cur_all.index:
            kind = "pushed_out"
        else:
            kind = "won" if o.loc[did, "outcome"] == "won" else "lost"
        moves[kind] -= x.amount
        detail.append((did, kind, -x.amount))
    advanced = 0
    for did in cur.index.intersection(prev.index):
        d = cur.loc[did, "amount"] - prev.loc[did, "amount"]
        if d:
            moves["amount_change"] += d
            detail.append((did, "amount_change", d))
        if order[cur.loc[did, "stage"]] > order[prev.loc[did, "stage"]]:
            advanced += 1
    start, end = float(prev.amount.sum()), float(cur.amount.sum())
    rows = [("Pipeline last week", start)] + [(k, v) for k, v in moves.items()] + [("Pipeline this week", end)]
    wf = pd.DataFrame(rows, columns=["step", "amount"])
    meta = {"start": start, "end": end, "advanced_deals": advanced,
            "detail": pd.DataFrame(detail, columns=["deal_id", "movement", "amount"])}
    return wf, meta


# ------------------------------------------------------------------ at risk / rep vs model
def at_risk(q) -> pd.DataFrame:
    f = pd.read_csv(OUT / "stage1" / "flagged_deals.csv", parse_dates=["close_date"])
    f = f[f.close_date.dt.to_period("Q") == q]
    return f[["deal_id", "account_name", "owner", "stage", "amount", "forecast_category", "flag_reasons",
              "ai_verdict", "proposed_fix"]].sort_values("amount", ascending=False)


def rep_vs_model(q, week: int) -> pd.DataFrame:
    raw = F.in_quarter(F.pipeline(TODAY, False), q)
    clean = F.in_quarter(F.pipeline(TODAY, True), q)
    conv, _, _ = F.learn_conversion(q, week, True)
    share = rules()["rep_call"]["best_case_share"]
    rep = raw.groupby("owner").apply(lambda x: x[x.forecast_category == "commit"].amount.sum()
                                     + share * x[x.forecast_category == "best_case"].amount.sum())
    model = clean.groupby("owner").apply(lambda x: (x.amount * x.stage.map(conv)).sum())
    df = pd.DataFrame({"rep_call": rep, "model_call": model}).fillna(0).round(-2)
    df["difference"] = df.rep_call - df.model_call
    df["inspect"] = (df.difference.abs() >= 20_000) & (
        df.difference.abs() >= 0.25 * df[["rep_call", "model_call"]].max(axis=1))
    return df.reset_index().sort_values("difference", ascending=False)


# ------------------------------------------------------------------ memo
SYSTEM = """You write the weekly forecast memo for a CRO. Use ONLY the facts in the JSON you are given.
Never calculate, round differently, or invent a number: every figure you write must appear in the facts exactly as written there.
Plain English, short sentences, no jargon, no em dashes. Structure:
1) The number (2 sentences). 2) What moved this week (2 to 3 sentences). 3) Where the forecast is at risk (2 to 3 sentences, name deal IDs).
4) Actions for this week (3 bullets, each with an owner and a deal ID or rep). Under 230 words."""


def memo_facts(num, wf, meta, risk, rvm) -> dict:
    f = {k: (money(v) if isinstance(v, (int, float)) and k not in ("week", "band_pct", "coverage_ratio") else v)
         for k, v in num.items()}
    f["band_pct"] = f"{num['band_pct']}%"
    f["coverage_ratio"] = f"{num['coverage_ratio']}x"
    f["method"] = {"historical": "historical conversion", "stage_weighted": "stage-weighted",
                   "rep_call": "rep call"}[num["method"]]
    f["waterfall"] = {r.step: money(r.amount) for r in wf.itertuples()}
    f["deals_advanced_this_week"] = meta["advanced_deals"]
    f["at_risk_deals"] = [{"deal_id": r.deal_id, "owner": r.owner, "amount": money(r.amount),
                           "flags": r.flag_reasons, "ai_verdict": r.ai_verdict, "proposed_fix": r.proposed_fix}
                          for r in risk.head(5).itertuples()]
    f["at_risk_total"] = money(risk.amount.sum())
    f["at_risk_count"] = len(risk)
    ins = rvm[rvm.inspect]
    f["rep_vs_model_gaps"] = [{"owner": r.owner, "rep_call": money(r.rep_call), "model_call": money(r.model_call)}
                              for r in ins.itertuples()]
    return f


def template_memo(f: dict) -> str:
    w = f["waterfall"]
    lines = [f"## Forecast memo, {f['quarter']}, week {f['week']}", "",
             f"**The number.** I recommend calling {f['recommended_forecast']} against a target of {f['target']}, "
             f"with a likely range of {f['range_low']} to {f['range_high']}. We have won {f['won_so_far']} so far.", "",
             f"**What moved.** In-quarter pipeline went from {w['Pipeline last week']} to {w['Pipeline this week']}.", "",
             f"**At risk.** {f['at_risk_count']} in-quarter deals worth {f['at_risk_total']} are flagged by the hygiene check.", "",
             "**Actions this week.**"]
    for d in f["at_risk_deals"][:3]:
        lines.append(f"- {d['owner']}: {d['proposed_fix']} ({d['deal_id']}).")
    return "\n".join(lines)


def reconcile(memo: str, facts: dict, wf: pd.DataFrame, meta: dict, num: dict) -> pd.DataFrame:
    checks = []
    fact_text = json.dumps(facts)
    figures = re.findall(r"-?\$\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?%|\d+(?:\.\d+)?x\b", memo)
    unknown = [x for x in figures if x not in fact_text]
    checks.append(("every figure in the memo is in the computed facts", not unknown,
                   f"{len(figures)} figures checked" + (f"; not found: {unknown}" if unknown else "")))
    total = wf.amount.iloc[0] + wf.amount.iloc[1:-1].sum()
    checks.append(("waterfall adds up from last week to this week", abs(total - wf.amount.iloc[-1]) < 1,
                   f"{money(wf.amount.iloc[0])} + movements = {money(total)} vs {money(wf.amount.iloc[-1])}"))
    det = meta["detail"].amount.sum()
    checks.append(("deal-level movements match the waterfall", abs(det - wf.amount.iloc[1:-1].sum()) < 1,
                   f"{len(meta['detail'])} deal movements"))
    checks.append(("range contains the recommended forecast", num["range_low"] <= num["recommended_forecast"] <= num["range_high"], ""))
    ids = re.findall(r"D\d{4}", memo)
    known = {d["deal_id"] for d in facts["at_risk_deals"]}
    checks.append(("every deal ID in the memo is a flagged at-risk deal", set(ids) <= known, f"{sorted(set(ids))}"))
    return pd.DataFrame(checks, columns=["check", "passed", "detail"])


def rubric(memo: str, facts: dict, recon: pd.DataFrame) -> pd.DataFrame:
    """Short rubric. 'accurate' comes from the reconciliation checks; the other two are simple, transparent tests."""
    moved = [k for k in facts["waterfall"] if k not in ("Pipeline last week", "Pipeline this week")
             and facts["waterfall"][k] != "$0"]
    explains = sum(1 for k in moved if k.replace("_", " ") in memo.lower() or facts["waterfall"][k] in memo)
    bullets = [l for l in memo.splitlines() if l.strip().startswith("-")]
    actions = sum(1 for b in bullets if re.search(r"D\d{4}", b) or any(r["owner"] in b for r in facts["rep_vs_model_gaps"]))
    return pd.DataFrame([
        ("accurate", bool(recon.passed.all()), "all reconciliation checks pass"),
        ("explains the change", explains >= 2, f"{explains} of {len(moved)} non-zero movements named"),
        ("names actions", actions >= 3, f"{actions} action bullets tied to a deal or rep"),
    ], columns=["criterion", "passed", "evidence"])


def write_memo(facts: dict, mode: str) -> tuple[str, str]:
    cache = OUT / "stage3" / "memo_ai_cache.json"
    prompt = json.dumps(facts, indent=1)
    h = hashlib.sha256((SYSTEM + prompt).encode()).hexdigest()[:16]
    if mode == "template":
        return template_memo(facts), "template (no AI)"
    saved = json.loads(cache.read_text()) if cache.exists() else {}
    if mode == "live":
        import anthropic
        msg = anthropic.Anthropic().messages.create(model=MODEL, max_tokens=700, temperature=0, system=SYSTEM,
                                                    messages=[{"role": "user", "content": prompt}])
        saved = {"hash": h, "model": MODEL, "response": msg.content[0].text}
        cache.write_text(json.dumps(saved, indent=1))
    if saved.get("hash") == h:
        return saved["response"], f"AI narrative ({saved['model']})"
    (OUT / "stage3" / "memo_prompt.json").write_text(json.dumps({"hash": h, "system": SYSTEM, "facts": facts}, indent=1))
    return template_memo(facts), "template (no saved AI response for these facts)"


# ------------------------------------------------------------------ outputs
def svg_waterfall(wf: pd.DataFrame) -> str:
    W, H, pad = 760, 280, 40
    vals = wf.amount.tolist()
    run, bars = 0.0, []
    for i, v in enumerate(vals):
        if i == 0 or i == len(vals) - 1:
            bars.append((0, v, "total"))
            run = v
        else:
            bars.append((run, run + v, "up" if v >= 0 else "down"))
            run += v
    top = max(max(a, b) for a, b, _ in bars) * 1.1
    bw = (W - 2 * pad) / len(bars)
    y = lambda v: H - pad - (v / top) * (H - 2 * pad)
    col = {"total": "#2a78d6", "up": "#1baf7a", "down": "#e34948"}
    out = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="Pipeline movement waterfall" style="width:100%;height:auto">']
    out.append(f'<line x1="{pad}" x2="{W - pad}" y1="{H - pad}" y2="{H - pad}" stroke="#c9c8c2"/>')
    for i, ((a, b, k), step) in enumerate(zip(bars, wf.step)):
        x = pad + i * bw + 6
        y0, y1 = y(max(a, b)), y(min(a, b))
        label = step.replace("_", " ")
        amt = vals[i]
        out.append(f'<rect x="{x:.1f}" y="{y0:.1f}" width="{bw - 12:.1f}" height="{max(y1 - y0, 1.5):.1f}" rx="3" fill="{col[k]}">'
                   f'<title>{html.escape(label)}: {money(amt)}</title></rect>')
        out.append(f'<text x="{x + (bw - 12) / 2:.1f}" y="{y0 - 6:.1f}" text-anchor="middle" font-size="11" fill="#0b0b0b">{money(amt) if amt else "$0"}</text>')
        out.append(f'<text x="{x + (bw - 12) / 2:.1f}" y="{H - pad + 16}" text-anchor="middle" font-size="11" fill="#52514e">{html.escape(label)}</text>')
    out.append("</svg>")
    return "".join(out)


def table(df: pd.DataFrame, money_cols=()) -> str:
    d = df.copy()
    for c in money_cols:
        d[c] = d[c].map(lambda v: money(v) if pd.notna(v) else "")
    head = "".join(f"<th>{html.escape(str(c).replace('_', ' '))}</th>" for c in d.columns)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(v))}</td>" for v in r) + "</tr>" for r in d.values)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def md_to_html(md: str) -> str:
    import markdown
    return markdown.markdown(md)


def build_html(num, wf, risk, rvm, memo, source, recon, rub) -> str:
    img = base64.b64encode((OUT / "stage2" / "error_by_method.png").read_bytes()).decode()
    tiles = [("Recommended forecast", money(num["recommended_forecast"]), f"range {money(num['range_low'])} to {money(num['range_high'])}"),
             ("Target", money(num["target"]), f"gap {money(num['gap_to_target'])}"),
             ("Won so far", money(num["won_so_far"]), f"week {num['week']} of 13"),
             ("Commit (clean)", money(num["commit_clean"]), f"rep call on raw CRM: {money(num['rep_call_raw'])}"),
             ("Coverage", f"{num['coverage_ratio']}x", "open in-quarter pipeline vs remaining target")]
    tiles_html = "".join(f'<div class="tile"><div class="k">{k}</div><div class="v">{v}</div><div class="s">{s}</div></div>'
                         for k, v, s in tiles)
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Forecast review pack {num['quarter']} week {num['week']}</title>
<style>
:root{{--bg:#fcfcfb;--ink:#0b0b0b;--muted:#52514e;--line:#e6e5e0;--accent:#2a78d6}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:32px 20px 64px}}
h1{{font-size:26px;margin:0 0 4px}} h2{{font-size:18px;margin:36px 0 10px}} .sub{{color:var(--muted)}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin-top:20px}}
.tile{{border:1px solid var(--line);border-radius:10px;padding:14px;background:#fff}}
.k{{color:var(--muted);font-size:13px}} .v{{font-size:24px;font-weight:650;margin:4px 0}} .s{{color:var(--muted);font-size:12.5px}}
table{{border-collapse:collapse;width:100%;font-size:13.5px;background:#fff}} th,td{{border-bottom:1px solid var(--line);padding:7px 8px;text-align:left;vertical-align:top}}
th{{color:var(--muted);font-weight:600}} .memo{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:6px 22px}}
.pass{{color:#008300}} .fail{{color:#e34948}} img{{max-width:100%}} .note{{color:var(--muted);font-size:12.5px}}
</style></head><body><main>
<h1>Forecast review pack, {num['quarter']}, week {num['week']}</h1>
<div class="sub">Snapshot {TODAY.date()} · built by one script in seconds · all data synthetic</div>
<div class="tiles">{tiles_html}</div>
<h2>Pipeline movement this week (in-quarter, raw CRM)</h2>{svg_waterfall(wf)}
<h2>Deals at risk</h2>{table(risk, ["amount"])}
<h2>Rep call vs model call</h2><p class="note">Rep call = commit + half of best case on the raw CRM. Model call = historical conversion on the clean pipeline. Rows marked True need inspection before the call.</p>
{table(rvm, ["rep_call", "model_call", "difference"])}
<h2>Memo for the CRO</h2><div class="memo">{md_to_html(memo)}</div><p class="note">Narrative: {html.escape(source)}. Every figure is checked against the computed facts below.</p>
<h2>Reconciliation checks</h2>{table(recon.assign(passed=recon.passed.map({True: "pass", False: "FAIL"})))}
<h2>Memo rubric</h2>{table(rub.assign(passed=rub.passed.map({True: "pass", False: "FAIL"})))}
<h2>How much to trust the number</h2><img src="data:image/png;base64,{img}" alt="Back-test error by method, raw vs clean pipeline">
<p class="note">Back-test over four quarters at weeks 2, 6 and 10. The range on the recommended forecast is the clean historical method's average error at the nearest scored week.</p>
</main></body></html>"""


def write_pdf(memo: str, path: Path):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    st = getSampleStyleSheet()
    story = []
    for line in memo.splitlines():
        t = html.escape(line.strip())
        t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
        if not t:
            story.append(Spacer(1, 6))
        elif t.startswith("## "):
            story.append(Paragraph(t[3:], st["Heading2"]))
        elif t.startswith("- "):
            story.append(Paragraph("• " + t[2:], st["BodyText"]))
        else:
            story.append(Paragraph(t, st["BodyText"]))
    story += [Spacer(1, 10), Paragraph("Synthetic demo data. Generated by revops-forecast-integrity, Stage 3.", st["Italic"])]
    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=56, rightMargin=56, topMargin=56).build(story)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ai", choices=["cache", "live", "template"], default="cache")
    args = ap.parse_args()
    out = OUT / "stage3"
    out.mkdir(parents=True, exist_ok=True)
    cur = json.loads((OUT / "stage2" / "forecast_current_quarter.json").read_text())
    q = quarter_of(TODAY)
    num = the_number(cur, q)
    wf, meta = waterfall(q)
    risk = at_risk(q)
    rvm = rep_vs_model(q, cur["week"])
    facts = memo_facts(num, wf, meta, risk, rvm)
    (out / "facts.json").write_text(json.dumps(facts, indent=1))
    memo, source = write_memo(facts, args.ai)
    recon = reconcile(memo, facts, wf, meta, num)
    rub = rubric(memo, facts, recon)
    wf.to_csv(out / "waterfall.csv", index=False)
    meta["detail"].to_csv(out / "waterfall_detail.csv", index=False)
    risk.to_csv(out / "at_risk.csv", index=False)
    rvm.to_csv(out / "rep_vs_model.csv", index=False)
    recon.to_csv(out / "reconciliation.csv", index=False)
    rub.to_csv(out / "memo_rubric.csv", index=False)
    (out / "forecast_memo.md").write_text(memo + f"\n\n_Synthetic demo data. Narrative: {source}._\n")
    write_pdf(memo, out / "forecast_memo.pdf")
    (out / "review_pack.html").write_text(build_html(num, wf, risk, rvm, memo, source, recon, rub))
    print(json.dumps(num, indent=1))
    print(wf.to_string(index=False))
    print(recon.to_string(index=False))
    print(rub.to_string(index=False))
    print("memo source:", source)


if __name__ == "__main__":
    main()
