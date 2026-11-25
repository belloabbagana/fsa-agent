"""'Ask the agent' (analyst mode only): a small model answers questions using ONLY the report it is given.

The report is turned into the same Markdown a person reads. The model must answer from that text, cite where (section letter or finding id), and say
"That is not in this report" when it is not there. Code then checks the answer: every number must appear in the report text (or be a year),
no recommendation or accusation wording, and the model identity is verified from the usage report. One retry, then the answer is withheld.
"""
import re

from fsa.findings import writer as fwr
from fsa.map.model_proposer import ask, verify_model
from fsa.report import assemble as asm

SYSTEM = (
    "You answer questions about ONE company-year report for a CFA-trained analyst. Rules:\n"
    "1. Use ONLY the report text supplied. Never use outside knowledge, never guess, never calculate a new number. If the answer is not in the report, reply exactly: That is not in this report.\n"
    "2. Copy numbers exactly as the report shows them. Say where the answer comes from, in brackets, using a section letter (for example [Section E]) or a finding id (for example [D-01]).\n"
    "3. Neutral wording. Findings are items to examine, never accusations. Never say buy, sell, hold, recommend, undervalued or overvalued, and give no investment advice.\n"
    "4. Keep the answer to 2 to 6 sentences. Plain words, no identifiers with underscores.")


def report_text(rep: dict) -> str:
    return asm.to_markdown(rep)


def check_answer(text: str, answer: str, question: str) -> list[str]:
    allowed = {n.replace(",", "") for n in fwr.NUMBER.findall(text)} | {n.replace(",", "") for n in fwr.NUMBER.findall(question)}
    problems = []
    bad = sorted({n for n in fwr.NUMBER.findall(answer) if n.replace(",", "") not in allowed})
    if bad:
        problems.append("numbers not found in the report: " + ", ".join(bad))
    if fwr.BANNED.search(answer):
        problems.append("banned wording: " + fwr.BANNED.search(answer).group(0))
    if re.search(r"[a-z]+_[a-z]+", answer):
        problems.append("an identifier with an underscore was used instead of plain words")
    return problems


ADVICE = re.compile(r"\b(buy|sell|hold|invest|recommend\w*|should i|worth buying|good investment|price target|undervalued|overvalued)\b", re.I)
ADVICE_REPLY = ("I do not give buy, sell or hold views or investment advice. This is educational analysis: it lists items to examine, with the figures and sources behind each. "
                "I can tell you what the report says about the company's earnings quality, cash flow, leverage or any finding.")


def answer_question(rep: dict, question: str, model: str = "haiku") -> dict:
    if ADVICE.search(question):                       # a standing rule (CLAUDE.md 6), so no model call is needed
        return {"status": "answered", "answer": ADVICE_REPLY, "model": ["none (fixed reply)"], "cost_usd": 0.0, "attempts": 0}
    text = report_text(rep)
    base = f"REPORT TEXT:\n{text}\n\nQUESTION: {question}\n\nAnswer from the report text only."
    prompt, used, cost, problems = base, set(), 0.0, []
    for attempt in (1, 2):
        ans = ask(prompt, model, system=SYSTEM)
        if ans.is_error:
            raise RuntimeError(f"the model call failed: {ans.error_text}")
        used |= verify_model(ans, model)
        cost += ans.cost_usd or 0.0
        problems = check_answer(text, ans.text, question)
        if not problems:
            return {"status": "answered", "answer": ans.text.strip(), "model": sorted(used), "cost_usd": cost, "attempts": attempt}
        prompt = base + "\n\nYour previous answer was rejected by code for these reasons; fix them and answer again:\n- " + "\n- ".join(problems)
    return {"status": "withheld", "answer": None, "problems": problems, "model": sorted(used), "cost_usd": cost, "attempts": 2}
