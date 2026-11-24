"""Findings writer: a model turns a candidate record into plain prose; code checks the prose before it is kept.

The model never decides whether something is a finding (the rule did) and never calculates (code did). It receives pre-formatted
figures and may only restate them. Code then verifies: every number in the text is one of the supplied figures (or the year, or a
threshold in the rule), and no accusation or recommendation wording appears. One retry with the problems listed; after that the
finding is kept as 'needs a person' with the rule's own wording, never with unchecked text.
"""
import json
import re

from fsa.map.model_proposer import ask, verify_model

SYSTEM = (
    "You write one short finding for an equity and credit analyst, from the facts supplied. Rules you must follow:\n"
    "1. Use ONLY the figures given, copied exactly as shown in 'show'. Never calculate, convert units, round differently, "
    "or state a percentage, difference or ratio that is not listed. Amounts are N millions as supplied.\n"
    "2. Neutral wording. This is an item to examine, never an accusation. Do not say or imply fraud, manipulation, misstatement, deception "
    "or wrongdoing. Never say buy, sell, hold, undervalued, overvalued or recommend anything.\n"
    "3. State what the figures show (observation), then why a CFA-trained analyst would look further (why_it_matters). "
    "Do not answer the question and do not add facts from outside the list. Where a figure is unavailable, say so.\n"
    "4. Describe a ratio in plain words with its unit (for example 'operating cash flow was 0.58 times profit', or a percentage if the unit is %), never by its identifier or with underscores. Account amounts are N millions.\n"
    "5. Wording discipline: no judgement words that the figures do not establish (large, sharp, heavy, significant, huge, concerning, improvement, favourable); say what the figure is and let the reader judge. Quote figures exactly as shown, never as 'a third' or 'about half'. If what_triggered_it uses >= say 'met or exceeded', never 'exceeded'. Do not use 'therefore', 'because', 'driven by' or 'main contributor' unless a supplied figure shows it; present causes only as things to examine. Do not rank or order items by size unless the figures show that order; list them in the order supplied and do not say 'followed by'. Do not say a flow is 'new borrowing', 'debt' or 'repayment' beyond what its label says (a gross inflow is not net debt). Describe a trigger as a threshold the figure reached ('reached the 2% threshold'), never as something that should or ought to hold. Write a negative cash flow as an outflow of N<amount> without the minus sign, always with the N prefix. Do not mention any measure that is not in the figures list. Use each figure's meaning, if given, to say what it compares, with its unit (pp means percentage points).\n"
    "6. observation: 2 to 4 sentences. why_it_matters: 1 to 3 sentences.\n"
    'Reply with JSON only: {"observation": "...", "why_it_matters": "..."}'
)

BANNED = re.compile(r"\b(fraud\w*|manipulat\w*|misstat\w*|decei\w*|scam\w*|cook\w*|buy|sell|hold(?:ing)?\s+rating|recommend\w*|undervalued|overvalued|wrongdoing|cheat\w*)\b", re.I)
NUMBER = re.compile(r"(?<![\d.,])\d[\d,]*(?:\.\d+)?")


def show(v: float) -> str:
    a = abs(v)
    return f"{v:,.0f}" if a >= 100 else (f"{v:.1f}" if a >= 10 else f"{v:.2f}")


def prompt_for(rec: dict) -> str:
    figures = [{"name": f["name"], "show": show(f["value"]) if f["value"] is not None else "not available",
                **({"unit": f["unit"]} if f.get("unit") else {}), **({"meaning": f["meaning"]} if f.get("meaning") else {}), **({"where": "; ".join(f"{s['statement']} p.{s['page']} '{s['label']}'" for s in f["source"])} if isinstance(f.get("source"), list) else {})}
               for f in rec["facts"]]
    body = {"company": rec["ticker"], "fiscal_year": rec["fiscal_year"], "amounts_unit": "N millions (account amounts); ratios carry their own unit", "title": rec["title"], "what_triggered_it": rec["trigger"],
            "question_to_be_asked_later": rec["question"], "figures": figures}
    return "Write the finding for this item.\n" + json.dumps(body, indent=1)


def allowed_numbers(rec: dict) -> set:
    ok = {str(rec["fiscal_year"]), str(rec["fiscal_year"] - 1)}
    for f in rec["facts"]:
        v = f["value"]
        if v is None:
            continue
        for s in {show(v), show(abs(v))}:
            ok |= {s, s.replace(",", "")}
        for d in (0, 1, 2):
            r = f"{abs(v):.{d}f}"
            ok |= {r, f"{abs(v):,.{d}f}"}
    for n in NUMBER.findall(rec["trigger"] + " " + rec["title"] + " " + rec["question"]):
        ok.add(n.replace(",", ""))
    return ok


def check_text(rec: dict, text: str) -> list[str]:
    problems = [f"wording not allowed: '{m.group(0)}'" for m in BANNED.finditer(text)]
    ok = allowed_numbers(rec)
    for n in NUMBER.findall(text):
        if n.replace(",", "").rstrip(".") not in {x.replace(",", "") for x in ok}:
            problems.append(f"number not in the supplied figures: {n}")
    return problems


def parse(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return d if isinstance(d.get("observation"), str) and isinstance(d.get("why_it_matters"), str) else None


def write_one(rec: dict, model: str) -> dict:
    """Returns the record with observation, why_it_matters, written_by and check results. Raises if a different model answered."""
    base = prompt_for(rec)
    prompt, problems, used, cost = base, [], set(), 0.0
    for attempt in (1, 2):
        ans = ask(prompt, model, system=SYSTEM)
        if ans.is_error:
            raise RuntimeError(f"the model call failed: {ans.error_text}")
        used |= verify_model(ans, model)
        cost += ans.cost_usd or 0.0
        got = parse(ans.text)
        problems = ["reply was not the JSON asked for"] if got is None else check_text(rec, got["observation"] + " " + got["why_it_matters"])
        if not problems:
            return rec | {"observation": got["observation"].strip(), "why_it_matters": got["why_it_matters"].strip(), "written_by": sorted(used),
                          "text_checks": "passed", "attempts": attempt, "cost_usd": cost, "status": "written"}
        prompt = base + "\n\nYour previous answer was rejected by code for these reasons; fix them and answer again:\n- " + "\n- ".join(problems)
    return rec | {"observation": None, "why_it_matters": None, "written_by": sorted(used), "text_checks": "failed: " + "; ".join(problems),
                  "attempts": 2, "cost_usd": cost, "status": "needs a person"}


def to_markdown(items: list[dict], ticker: str, fy: int) -> str:
    lines = [f"# Findings: {ticker} FY{fy}", "", "> Items to examine, not accusations. Educational analysis, not investment advice. Numbers come from the company's published statements as mapped by code; wording was drafted by a model and checked by code.", ""]
    for r in items:
        lines += [f"## {r['id']} · {r['title']} · {str(r['materiality']).upper()} · {r['type']}", ""]
        if r["status"] == "needs a person":
            lines += [f"*The drafted wording failed the code checks ({r['text_checks']}). Use the facts in finding_candidates.md.*", ""]
        else:
            lines += [f"**What the figures show.** {r['observation']}", "", f"**Why it matters.** {r['why_it_matters']}", ""]
        from fsa.findings.review import markdown_block
        lines += markdown_block(r)
        lines += [f"**Question for management or public sources ({r['who_answers']}).** {r['question']}", "", "**Hypotheses and evidence**", ""]
        lines += [f"- {h['h']}  \n  would be confirmed by: {h['confirm']}; ruled out by: {h['rule_out']}" for h in r["hypotheses"]]
        lines += ["", "**Figures and sources**", ""]
        for f in r["facts"]:
            s = f.get("source")
            ref = "; ".join(f"{x['statement']} p.{x['page']} '{x['label']}'" for x in s) if isinstance(s, list) else (s or "")
            lines.append(f"- {f['name']}: {'n/a' if f['value'] is None else show(f['value'])}" + (f" ({ref})" if ref else ""))
        lines.append("")
    return "\n".join(lines)
