"""Opus review of drafted findings. A second reader, not an author.

The reviewer sees the same figures and the drafted text and says: does the text follow from the figures, is the tone neutral,
and which explanations (especially benign, Nigeria-specific ones) the rule's hypotheses leave out. It never rewrites the finding,
never calculates, and never changes the trigger or the materiality. Its notes are attached for Bello to read; a person decides.
Code checks the reviewer's own text the same way it checks the writer's (numbers must come from the figures; no accusation or advice wording).
"""
import json
import re

from fsa.findings.writer import check_text, prompt_for, show
from fsa.map.model_proposer import ask, verify_model

SYSTEM = (
    "You are a senior CFA-trained reviewer checking a junior analyst's drafted finding on a Nigerian listed company. You do not rewrite it. Check:\n"
    "1. SUPPORT: does every statement in the draft follow from the figures supplied? Flag anything stated that the figures do not show.\n"
    "2. TONE: is it neutral, an item to examine and never an accusation, with no buy/sell/hold or valuation view?\n"
    "3. OVERLOOKED: name up to three explanations, especially benign ones tied to Nigeria (naira devaluation, high inflation, "
    "IAS 29 or IAS 21 effects, FX-denominated balances, sector norms), that the listed hypotheses do not cover.\n"
    "Rules: use only figures supplied, copied as shown; never calculate or introduce new numbers; never use words like fraud, manipulation, "
    "misstatement, buy, sell or recommend. Keep each point under 30 words.\n"
    'Reply with JSON only: {"verdict": "supported" | "needs_changes" | "not_supported", "problems": ["..."], '
    '"overlooked_explanations": ["..."], "context_for_analyst": "one sentence"}'
)
VERDICTS = {"supported", "needs_changes", "not_supported"}


def review_prompt(f: dict) -> str:
    hyp = [h["h"] for h in f["hypotheses"]]
    return (prompt_for(f).replace("Write the finding for this item.", "Review the drafted finding for this item.")
            + "\n\nDrafted text:\n" + json.dumps({"observation": f["observation"], "why_it_matters": f["why_it_matters"]}, indent=1)
            + "\n\nHypotheses already listed by the rule: " + json.dumps(hyp))


def parse(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    ok = d.get("verdict") in VERDICTS and isinstance(d.get("problems"), list) and isinstance(d.get("overlooked_explanations"), list) and isinstance(d.get("context_for_analyst"), str)
    return d if ok else None


def review_text_problems(f: dict, d: dict) -> list[str]:
    parts = d["problems"] + d["overlooked_explanations"] + [d["context_for_analyst"]]
    return check_text(f, " ".join(str(p) for p in parts))


def review_one(f: dict, model: str) -> dict:
    """Adds a 'review' block. Raises if a different model answered. Never edits the finding's own text."""
    base = review_prompt(f)
    prompt, problems, used, cost = base, [], set(), 0.0
    for attempt in (1, 2):
        ans = ask(prompt, model, system=SYSTEM)
        if ans.is_error:
            raise RuntimeError(f"the model call failed: {ans.error_text}")
        used |= verify_model(ans, model)
        cost += ans.cost_usd or 0.0
        got = parse(ans.text)
        problems = ["reply was not the JSON asked for"] if got is None else review_text_problems(f, got)
        if not problems:
            return f | {"review": got | {"reviewed_by": sorted(used), "text_checks": "passed", "attempts": attempt, "cost_usd": cost}}
        prompt = base + "\n\nYour previous answer was rejected by code for these reasons; fix them and answer again:\n- " + "\n- ".join(problems)
    return f | {"review": {"verdict": "not completed", "problems": [], "overlooked_explanations": [], "context_for_analyst": "",
                           "reviewed_by": sorted(used), "text_checks": "failed: " + "; ".join(problems), "attempts": 2, "cost_usd": cost}}


def markdown_block(f: dict) -> list[str]:
    r = f.get("review")
    if not r:
        return []
    lines = [f"**Review ({', '.join(r['reviewed_by'])}): {r['verdict'].replace('_', ' ')}.**"]
    if r["text_checks"] != "passed":
        return lines + [f"*The reviewer's notes failed the code checks ({r['text_checks']}); not shown.*", ""]
    lines += [f"- Problem: {p}" for p in r["problems"]] + [f"- Possible explanation not yet listed: {e}" for e in r["overlooked_explanations"]]
    if r["context_for_analyst"]:
        lines.append(f"- For the analyst: {r['context_for_analyst']}")
    return lines + [""]
