"""
The deal desk policy as code: given one quote, which approvals does it need?

Every rule comes from data/deal_desk_policy.yaml, so finance or sales leadership
can change a threshold without touching this file.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

# words in free-text notes that usually mean a non-standard clause was promised
CLAUSE_WORDS = {
    "custom_sla": [r"\bsla\b", r"uptime guarantee", r"response time commitment"],
    "termination_for_convenience": [r"terminat\w* for convenience", r"cancel any ?time", r"exit clause"],
    "most_favoured_nation": [r"\bmfn\b", r"most favou?red", r"best price guarantee"],
    "uncapped_liability": [r"uncapped liability", r"unlimited liability"],
}
NEGATION = re.compile(r"\b(no|not|without|declined|removed)\b[^.]{0,25}$")


@lru_cache(maxsize=1)
def policy() -> dict:
    with open(ROOT / "data" / "deal_desk_policy.yaml") as f:
        return yaml.safe_load(f)


def rank(role: str) -> int:
    order = policy()["approvers"]
    return order.index(role) if role in order else -1


def discount_approver(discount_pct: float) -> str | None:
    for band in policy()["discount_bands"]:
        if discount_pct <= band["max_pct"] + 1e-9:
            return band["approver"]
    return None  # above the hard ceiling


def clauses_in_notes(notes: str) -> set[str]:
    """Find promised clauses written in notes instead of the clause field. Skips negated mentions."""
    found = set()
    text = (notes or "").lower()
    for clause, pats in CLAUSE_WORDS.items():
        for p in pats:
            for m in re.finditer(p, text):
                if not NEGATION.search(text[max(0, m.start() - 40):m.start()]):
                    found.add(clause)
    return found


def required_approvals(q: dict) -> tuple[set[str], list[str]]:
    """Return (roles that must approve, hard policy breaks that no approval can fix)."""
    p = policy()
    need, hard = set(), []
    appr = discount_approver(q["discount_pct"])
    if appr is None:
        hard.append(f"discount {q['discount_pct']:.1f}% is above the {p['hard_ceiling_pct']}% ceiling")
    elif appr != "rep":
        need.add(appr)
    extra = p["payment_terms"].get(int(q["payment_terms_days"]), "finance")
    if extra:
        need.add(extra)
    if q["free_months"] > p["free_months"]["hard_ceiling"]:
        hard.append(f"{q['free_months']} free months is above the ceiling of {p['free_months']['hard_ceiling']}")
    elif q["free_months"] > p["free_months"]["max_without_approval"]:
        need.add(p["free_months"]["approver"])
    if int(q["term_months"]) not in p["term_months"]["standard"]:
        need.add(p["term_months"]["non_standard_approver"])
    clauses = {c for c in str(q.get("clauses", "") or "").split(";") if c} | clauses_in_notes(q.get("notes", ""))
    if clauses & set(p["non_standard_clauses"]):
        need.add("legal")
    return need, hard


def normalise_role(raw: str) -> str:
    r = raw.strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {"vp": "vp_sales", "vp_of_sales": "vp_sales", "manager": "sales_manager", "sm": "sales_manager",
               "fin": "finance", "finance_team": "finance", "legal_team": "legal", "chief_revenue_officer": "cro"}
    return aliases.get(r, r)


def is_covered(role: str, obtained: set[str]) -> bool:
    """A more senior sales approver covers a junior one (a CRO can approve a manager-level discount)."""
    if role in obtained:
        return True
    if rank(role) >= 0:
        return any(rank(o) > rank(role) for o in obtained)
    return False
