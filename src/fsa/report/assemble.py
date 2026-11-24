"""Assemble one company-year report (Markdown plus JSON, report.v1) from pieces that already exist.

Nothing new is calculated or written by a model here. The assembler only gathers:
  - scorecard rows (rules/report_layout.yaml), evaluated by the same code that computes ratios and triggers;
  - the written and reviewed findings (findings.json) and the data notes (disclosures.json);
  - plain statements of what is NOT generated yet (ratings, data-quality grade, conclusion), never filled in by guesswork.
"""
import ast
import json
from datetime import date

import yaml

from fsa.analyse.ratios import NotMeaningful
from fsa.findings.review import markdown_block
from fsa.findings.triggers import TriggerEvaluator, build_context
from fsa.findings.writer import show
from fsa.warehouse.db import ROOT

LAYOUT = ROOT / "rules" / "report_layout.yaml"
VERSION = "0.1-draft"
NOT_GENERATED = {
    "ratings": "Not generated yet. The Financial Health, Earnings Quality and Forensic Risk ratings need rating rules that the analyst has not yet set.",
    "data_quality_grade": "Not graded yet. The High, Moderate or Low grade needs grading rules that the analyst has not yet set.",
    "conclusion": "Not generated yet. The final conclusion waits for the ratings.",
}
ORDER = {"high": 0, "medium": 1, "low": 2, "unrated": 3}


def load_layout() -> dict:
    return yaml.safe_load(LAYOUT.read_text())


def fmt(value, unit: str) -> str:
    if value is None:
        return "n/a"
    if unit == "m":
        return f"{value:,.0f}"
    return {"pct": f"{value:,.1f}%", "x": f"{value:,.2f}x", "days": f"{value:,.0f}"}.get(unit, f"{value:,.2f}")


def _cell(ev: TriggerEvaluator, expr: str):
    """(value, note). A ratio that is not meaningful says so; a missing input says n/a. Never zero."""
    try:
        return ev.ev(ast.parse(expr, mode="eval").body), None
    except NotMeaningful as e:
        return None, f"N/M: {e}"
    except (KeyError, ValueError, ZeroDivisionError) as e:
        return None, f"n/a: {e}"


def scorecards(con, chart, ratio_rules, layout: dict, ticker: str, fy: int) -> dict:
    years, contexts = [], {}
    for y in range(fy - layout["years_shown"] + 1, fy + 1):
        try:
            cur, prior, now, known, extra, lookup = build_context(con, chart, ratio_rules, ticker, y)
        except SystemExit:
            continue                                            # no settled mapping for that year
        contexts[y] = TriggerEvaluator(cur, prior, ratio_rules["metrics"], now, known, extra, lookup)
        years.append(y)
    out = {}
    for letter, card in layout["scorecards"].items():
        rows = []
        for r in card["rows"]:
            cells = {}
            for y in years:
                v, note = _cell(contexts[y], r["expr"])
                cells[str(y)] = {"value": v, **({"note": note} if note else {})}
            rows.append({"label": r["label"], "expr": r["expr"], "unit": r["unit"], "values": cells})
        out[letter] = {"title": card["title"], "years": years, "rows": rows}
    return out


def finding_record(f: dict) -> dict:
    """One finding as the JSON record the spec asks for (id, section, observation, numbers with sources, question, why, hypotheses, ...)."""
    written = f["status"] == "written"
    r = f.get("review")
    return {
        "id": f["id"], "section": f["section"], "title": f["title"],
        "observation": f.get("observation") if written else None,
        "why_it_matters": f.get("why_it_matters") if written else None,
        "numbers": [{"name": x["name"], "value": x["value"], **({"unit": x["unit"]} if x.get("unit") else {}),
                     **({"sources": x["source"]} if x.get("source") else {})} for x in f["facts"]],
        "trigger": f["trigger"],
        "question": f["question"], "why_i_am_asking": f["why"], "hypotheses": f["hypotheses"],
        "materiality": f["materiality"], "type": f["type"], "who_answers": f["who_answers"], "docs_needed": f.get("docs_needed") or [],
        "status": "open", "wording_status": f["status"], "version": VERSION,
        "review": None if not r else {"verdict": r["verdict"], "reviewed_by": r["reviewed_by"], "notes_passed_checks": r["text_checks"] == "passed",
                                      **({"problems": r["problems"], "overlooked_explanations": r["overlooked_explanations"],
                                          "context_for_analyst": r["context_for_analyst"]} if r["text_checks"] == "passed" else {})},
        "disclaimer": f["disclaimer"],
    }


def build_report(con, chart, ratio_rules, company: dict, ticker: str, fy: int) -> dict:
    base = ROOT / "data" / "interim" / ticker / f"FY{fy}"
    layout = load_layout()
    findings = sorted((finding_record(f) for f in json.loads((base / "findings.json").read_text())) if (base / "findings.json").exists() else [],
                      key=lambda r: (ORDER.get(r["materiality"], 9), r["section"], r["id"]))
    notes = json.loads((base / "disclosures.json").read_text()) if (base / "disclosures.json").exists() else []
    ratings = json.loads((base / "ratings.json").read_text()) if (base / "ratings.json").exists() else None
    not_generated = {k: v for k, v in NOT_GENERATED.items() if ratings is None}
    return {
        "schema": "report.v1", "version": VERSION, "status": "draft", "generated_on": date.today().isoformat(),
        "ticker": ticker, "company": company["name"], "fiscal_year": fy, "amounts_unit": "N millions unless stated; nominal, Group",
        "disclaimer": "Educational analysis, not investment advice. Items are matters to examine, never accusations. No buy, sell or hold view is given.",
        "not_generated": not_generated, "ratings": ratings, "scorecards": scorecards(con, chart, ratio_rules, layout, ticker, fy),
        "findings": findings, "data_notes": notes,
        "inputs_missing": [name for name, ok in (("findings.json", (base / "findings.json").exists()), ("disclosures.json", (base / "disclosures.json").exists()), ("ratings.json", (base / "ratings.json").exists())) if not ok],
        "section_titles": layout["section_titles"],
    }


def _table(card: dict) -> list[str]:
    ys = card["years"]
    lines = ["| | " + " | ".join(f"FY{y}" for y in ys) + " |", "|---|" + "---:|" * len(ys)]
    for r in card["rows"]:
        lines.append(f"| {r['label']} | " + " | ".join(fmt(r["values"][str(y)]["value"], r["unit"]) if r["values"][str(y)]["value"] is not None
                                                      else ("N/M" if str(r["values"][str(y)].get("note", "")).startswith("N/M") else "n/a") for y in ys) + " |")
    return lines + [""]


def to_markdown(rep: dict) -> str:
    T, titles = rep["ticker"], rep["section_titles"]
    L = [f"# {rep['company']} ({T}) · FY{rep['fiscal_year']} · financial statement analysis", "",
         f"> **DRAFT {rep['version']}.** {rep['disclaimer']} Amounts: {rep['amounts_unit']}.", ""]
    if rep["inputs_missing"]:
        L += [f"> Missing inputs for this report: {', '.join(rep['inputs_missing'])}.", ""]
    rt = rep.get("ratings")
    if rt:
        eq, fh, fr = rt["earnings_quality"], rt["financial_health"], rt["forensic_risk"]
        rating_lines = [f"- **Financial Health:** {fh['rating']} (pillars: " + ", ".join(f"{k} {v['level'] or 'not rated'}" for k, v in fh["pillars"].items()) + ")",
                        f"- **Earnings Quality:** {eq['rating']} ({eq['points']} demerit points from {eq['tests_run']} tests run)",
                        f"- **Forensic Risk:** {fr['rating']}. {fr['note']} {fr['red_not_assignable'] or ''}".rstrip(),
                        f"- **Data-quality grade:** {rt['data_quality']['grade']}",
                        "- The three ratings are kept separate on purpose. They are drafts from `rules/ratings.yaml`, counts of named tests, not verdicts."]
    else:
        rating_lines = [f"- **Ratings:** {rep['not_generated']['ratings']}", f"- **Data-quality grade:** {rep['not_generated']['data_quality_grade']}"]
    L += [f"## A. {titles['A']}", ""] + rating_lines + [
          f"- **Findings:** {len(rep['findings'])} items to examine: "
          + ", ".join(f"{sum(f['materiality'] == m for f in rep['findings'])} {m}" for m in ("high", "medium", "low")) + ".", ""]
    for letter in "BCDEFGHIJK":
        L += [f"## {letter}. {titles[letter]}", ""]
        if letter in rep["scorecards"]:
            L += _table(rep["scorecards"][letter])
        fs = [f for f in rep["findings"] if f["section"] == letter]
        if fs:
            L += ["**Items to examine in this section** (full text in section L)", ""] + [f"- {f['id']} · {f['materiality'].upper()} · {f['title']}" for f in fs] + [""]
        elif letter not in rep["scorecards"]:
            L += ["No items triggered for this section by the current rules. This does not show that nothing is worth examining.", ""]
    L += [f"## L. {titles['L']}", ""]
    for f in rep["findings"]:
        L += [f"### {f['id']} · {f['title']} · {f['materiality'].upper()} · {f['type']}", ""]
        if f["wording_status"] == "written":
            L += [f"**What the figures show.** {f['observation']}", "", f"**Why it matters.** {f['why_it_matters']}", ""]
        else:
            L += ["*The drafted wording failed the code checks; see the facts below.*", ""]
        r = f["review"]
        if r:
            L += [f"**Review ({', '.join(r['reviewed_by'])}): {r['verdict'].replace('_', ' ')}.**"]
            if r["notes_passed_checks"]:
                L += [f"- Problem: {p}" for p in r["problems"]] + [f"- Possible explanation not yet listed: {e}" for e in r["overlooked_explanations"]]
                L += [f"- For the analyst: {r['context_for_analyst']}"] if r["context_for_analyst"] else []
            L += [""]
        L += [f"**Question ({f['who_answers']}).** {f['question']}", "", f"**Why I'm asking.** {f['why_i_am_asking']}", "", "**Hypotheses and evidence**", ""]
        L += [f"- {h['h']}  \n  confirmed by: {h['confirm']}; ruled out by: {h['rule_out']}" for h in f["hypotheses"]]
        L += ["", "**Figures and sources**", ""]
        for n in f["numbers"]:
            ref = "; ".join(f"{s['statement']} p.{s['page']} '{s['label']}'" for s in n["sources"]) if isinstance(n.get("sources"), list) else (n.get("sources") or "")
            L.append(f"- {n['name']}: {'n/a' if n['value'] is None else show(n['value'])}" + (f" ({ref})" if ref else ""))
        L += ["", f"**Documents to request:** {', '.join(f['docs_needed']) or 'none listed'} · **Status:** {f['status']} · rules {f['version']}", ""]
    if rt:
        L += [f"## M. {titles['M']}", "", f"**Category (draft, from the rating rules): {rt['conclusion']['category']}.** Trend measures: "
              + "; ".join(f"{x['measure']} {x['state']}" for x in rt["conclusion"]["trend"]) + ". This is a category from stated tests, not a narrative conclusion; the narrative waits for review.", "", "## Data notes and disclaimers", ""]
    else:
        L += [f"## M. {titles['M']}", "", rep["not_generated"]["conclusion"], "", "## Data notes and disclaimers", ""]
    for n in rep["data_notes"]:
        L.append(f"- **{n['category']}** ({n['severity']}): {n['text']}")
    return "\n".join(L) + "\n"
