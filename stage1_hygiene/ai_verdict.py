"""
Stage 1, step 3: one AI call per flagged deal.

The rules say WHAT is wrong with a record. This step asks WHY: is the deal
abandoned (the buyer has gone), mis-staged (it is further along in the CRM
than in reality), stalled (alive but not moving), or is the flag a false
alarm (no_issue)? It also drafts a short nudge to the deal owner.

Guardrails:
  - Only deals with a process flag go to the AI. Duplicates and missing data
    are data fixes, the rules handle them without AI.
  - Exactly one call per deal, no loops, no tools. Temperature 0.
  - Up to 3 retries on API errors. Output must be valid JSON with an allowed
    verdict; anything else falls back to "needs_review" instead of crashing.
  - The AI never changes the CRM. Its verdict and nudge go to a person in
    approvals.csv.

Modes:
  live   calls the Claude API (needs ANTHROPIC_API_KEY, pip install anthropic)
  cache  replays saved responses from outputs/stage1/ai_cache.jsonl, so anyone
         can rerun the demo without a key. A prompt with no saved response
         gets "needs_review".
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

import pandas as pd

VERDICTS = ["abandoned", "mis_staged", "stalled", "no_issue"]
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5")

SYSTEM = """You are a revenue operations analyst reviewing one CRM deal that a hygiene check flagged.
Decide which ONE verdict fits best:
- abandoned: the buyer has gone quiet and the deal is effectively dead, even if the record is still open.
- mis_staged: the deal is recorded further along (stage or forecast category) than the evidence supports.
- stalled: the deal is alive but not progressing; dates keep slipping or the close date is not being managed.
- no_issue: the flag is a false alarm and the deal looks healthy on the evidence.
Use only the facts given. A dated, specific next step in the future is strong evidence of life.
"Follow up" or an empty next step is weak evidence.
Reply with JSON only, no other text:
{"verdict": "<abandoned|mis_staged|stalled|no_issue>", "reason": "<max 25 words>", "owner_nudge": "<max 45 words, friendly, to the deal owner, one clear ask>"}"""


def build_prompt(row: pd.Series, history: pd.DataFrame, stage_def: str) -> str:
    h = history[(history.deal_id == row.deal_id) & (history.snapshot_date <= row.snapshot_date)]
    acts = sorted(h.last_activity_date.dropna().unique())[-6:]
    acts_txt = ", ".join(pd.Timestamp(a).date().isoformat() for a in acts) or "none recorded"
    close = row.close_date.date().isoformat() if pd.notna(row.close_date) else "blank"
    fields = ", ".join(f"{f}={'yes' if row[f] else 'no'}" for f in
                       ["pain_confirmed", "decision_maker_identified", "budget_confirmed", "paper_process_known"])
    amount = f"${row.amount:,.0f}" if pd.notna(row.amount) else "blank"
    return f"""Review date: {row.snapshot_date.date().isoformat()}
Deal: {row.deal_id} ({row.segment}), owner {row.owner}, amount {amount}
Stage: {row.stage} for {row.days_in_stage} days. Stage definition: {stage_def}
Forecast category: {row.forecast_category}
Close date: {close} (pushed later {row.close_date_pushes} times so far)
Last activity: {row.last_activity_date.date().isoformat()} ({row.days_since_activity} days ago). Recent activity dates: {acts_txt}
Next step: "{row.next_step or 'blank'}"
Qualification fields: {fields}
Flags raised by the rules check: {row.flag_reasons}"""


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256((SYSTEM + "\n\n" + prompt).encode()).hexdigest()[:16]


def parse(text: str) -> dict:
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        out = json.loads(text[start:end])
        if out.get("verdict") not in VERDICTS:
            raise ValueError("bad verdict")
        return {"verdict": out["verdict"], "reason": str(out.get("reason", ""))[:300],
                "owner_nudge": str(out.get("owner_nudge", ""))[:500]}
    except Exception:
        return {"verdict": "needs_review", "reason": "AI output could not be read, sent to a person",
                "owner_nudge": ""}


def call_claude(prompt: str) -> str:
    import anthropic  # only needed in live mode

    client = anthropic.Anthropic()
    for attempt in range(3):
        try:
            msg = client.messages.create(model=MODEL, max_tokens=300, temperature=0, system=SYSTEM,
                                         messages=[{"role": "user", "content": prompt}])
            return msg.content[0].text
        except Exception:
            if attempt == 2:
                return ""
            time.sleep(2 ** attempt)
    return ""


def load_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    return {r["hash"]: r for r in rows}


def run(flagged: pd.DataFrame, history: pd.DataFrame, stage_defs: dict, mode: str, cache_path: Path) -> pd.DataFrame:
    cache = load_cache(cache_path)
    new = []
    out = []
    for _, row in flagged.iterrows():
        prompt = build_prompt(row, history, stage_defs[row.stage])
        h = prompt_hash(prompt)
        if mode == "live" and h not in cache:
            text = call_claude(prompt)
            rec = {"hash": h, "deal_id": row.deal_id, "review_date": str(row.snapshot_date.date()),
                   "model": MODEL, "response": text}
            cache[h] = rec
            new.append(rec)
        text = cache.get(h, {}).get("response", "")
        res = parse(text) if text else {"verdict": "needs_review", "reason": "no AI response saved for this prompt",
                                        "owner_nudge": ""}
        out.append({"deal_id": row.deal_id, "audit_date": str(row.snapshot_date.date()), "prompt_hash": h,
                    "ai_verdict": res["verdict"], "ai_reason": res["reason"], "owner_nudge": res["owner_nudge"]})
    if new:
        with open(cache_path, "a") as f:
            for r in new:
                f.write(json.dumps(r) + "\n")
    return pd.DataFrame(out)


def export_prompts(flagged: pd.DataFrame, history: pd.DataFrame, stage_defs: dict, path: Path) -> int:
    """Write every prompt to a JSONL file (used to batch-run the AI step outside this script)."""
    with open(path, "w") as f:
        for _, row in flagged.iterrows():
            p = build_prompt(row, history, stage_defs[row.stage])
            f.write(json.dumps({"hash": prompt_hash(p), "deal_id": row.deal_id,
                                "review_date": str(row.snapshot_date.date()), "prompt": p}) + "\n")
    return len(flagged)
