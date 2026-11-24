"""Ask a Claude model to propose chart-of-accounts codes for printed lines, and PROVE which model answered.

Runs through the Claude Agent SDK on the analyst's own sign-in (no API key). Two safeguards:
 * the model that answered is read from the usage report, never taken from the request or from what the
   model says about itself, and the run stops if it is not the model asked for;
 * a proposed code that is not in the chart is rejected, never accepted.
Only printed labels and section headings are sent: no figures, no company data beyond the label text.
"""
import asyncio
import json
import re
import tempfile
from dataclasses import dataclass, field

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query

SYSTEM = (
    "You classify printed lines of audited financial statements (IFRS, Nigerian companies) into a fixed chart of accounts. "
    "Answer with a JSON array only, no prose, no code fences. For every line give: id, code, confidence, reason. "
    "code must be one code from the chart exactly as written, or NONE if no account fits. "
    "confidence is high, medium or low. reason is at most 12 words. "
    "Use the section heading to tell current from non-current. A printed total or subtotal maps to the matching subtotal code. "
    "Lines marked memo in the chart explain another line and are never summed."
)


class WrongModelError(RuntimeError):
    pass


@dataclass
class ModelAnswer:
    text: str
    models_seen: set = field(default_factory=set)      # model ids reported on the assistant messages
    models_billed: set = field(default_factory=set)    # model ids in the result's usage report
    cost_usd: float | None = None
    is_error: bool = False
    error_text: str = ""


def verify_model(answer: ModelAnswer, requested: str) -> set:
    """Raise unless every model that did work contains the requested family name (e.g. 'haiku')."""
    used = answer.models_seen | answer.models_billed
    if not used:
        raise WrongModelError("no model reported any usage: cannot confirm which model answered")
    want = requested.lower()
    bad = {m for m in used if want not in m.lower()}
    if bad:
        raise WrongModelError(f"asked for '{requested}' but these models did the work: {sorted(used)}")
    return used


async def _ask(prompt: str, model: str, system: str = SYSTEM) -> ModelAnswer:
    opts = ClaudeAgentOptions(
        model=model, system_prompt=system, max_turns=1, tools=[], allowed_tools=[],
        setting_sources=[], cwd=tempfile.mkdtemp(prefix="fsa-model-"),
    )
    ans = ModelAnswer(text="")
    async for msg in query(prompt=prompt, options=opts):
        if isinstance(msg, AssistantMessage):
            if msg.model:
                ans.models_seen.add(msg.model)
            ans.text += "".join(b.text for b in msg.content if isinstance(b, TextBlock))
        elif isinstance(msg, ResultMessage):
            if msg.model_usage:
                ans.models_billed |= set(msg.model_usage.keys())
            ans.cost_usd = msg.total_cost_usd
            ans.is_error = bool(msg.is_error)
            if msg.is_error:
                ans.error_text = str(msg.result or msg.errors or "")
    return ans


def ask(prompt: str, model: str, system: str = SYSTEM) -> ModelAnswer:
    return asyncio.run(_ask(prompt, model, system))


def model_check(model: str = "haiku") -> dict:
    """The smallest possible real call, used to prove which model really answers."""
    ans = ask("Reply with exactly one word: ready", model)
    if ans.is_error:
        raise RuntimeError(f"the model call failed: {ans.error_text}")
    used = verify_model(ans, model)
    return {"requested": model, "answered_by": sorted(used), "reply": ans.text.strip(), "cost_usd": ans.cost_usd}


# ---------------------------------------------------------------- mapping prompt and parsing
def chart_for_prompt(chart: dict) -> str:
    rows = []
    for a in chart["accounts"]:
        memo = " [memo]" if "memo" in a.get("tags", []) else ""
        rows.append(f"{a['code']} | {a['statement']} | {a['kind']} | {a['name']}{memo}")
    return "\n".join(rows)


def build_prompt(chart: dict, statement: str, lines: list[dict]) -> str:
    body = "\n".join(
        f"{ln['id']} | section: {ln['section'] or '-'} | label: {ln['label'] or '(blank: unlabelled subtotal)'}" for ln in lines)
    return (f"CHART OF ACCOUNTS (code | statement | kind | name):\n{chart_for_prompt(chart)}\n\n"
            f"PRINTED LINES of the {statement.replace('_', ' ')} (id | section heading | label):\n{body}\n\n"
            "Return the JSON array now.")


def parse_proposals(text: str, valid_codes: set, valid_ids: set) -> tuple[list[dict], list[str]]:
    """-> (accepted proposals, rejection notes). Invented codes and unknown ids are rejected."""
    notes, out = [], []
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        return [], ["no JSON array in the model's answer"]
    try:
        items = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return [], [f"unreadable JSON: {e}"]
    for it in items:
        i, code = str(it.get("id")), it.get("code")
        if i not in valid_ids:
            notes.append(f"unknown line id {i!r} ignored")
        elif code == "NONE" or code is None:
            out.append({"id": i, "code": None, "confidence": it.get("confidence"), "reason": it.get("reason", "")})
        elif code not in valid_codes:
            notes.append(f"line {i}: model invented code {code!r}: rejected")
            out.append({"id": i, "code": None, "confidence": "rejected", "reason": f"invented code {code}"})
        else:
            out.append({"id": i, "code": code, "confidence": it.get("confidence"), "reason": it.get("reason", "")})
    return out, notes
