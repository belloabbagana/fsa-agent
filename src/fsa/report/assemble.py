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


def peers_block(con, layout: dict, ticker: str, fy: int) -> dict | None:
    """Analyst-only peer comparison: a few ratios per category, company and each peer on the same third-party basis.
    A ratio is left out when the company's value or every peer's value is not meaningful (no empty comparisons)."""
    cfg = yaml.safe_load((ROOT / "config" / "companies.yaml").read_text())
    me = next((c for c in cfg["companies"] if c["ticker"] == ticker), None)
    if not me:
        return None
    peers = [c for c in cfg["companies"] if c.get("role") == "peer" and c.get("industry_peer_set") == me.get("industry_peer_set") and c.get("conflict_check") == "pass"]
    labels = {r["expr"]: r["label"] for g in layout["ratio_groups"].values() for r in g["rows"]}
    val = lambda t, rid: (lambda r: r[0] if r else None)(con.execute(
        "SELECT value FROM comparison_ratios WHERE ticker=? AND fiscal_year=? AND ratio_id=? AND status='ok'", [t, fy, rid]).fetchone())
    groups = {}
    for gname, ids in layout["peer_groups"].items():
        rows = []
        for rid in ids:
            mine = val(ticker, rid)
            theirs = {p["ticker"]: val(p["ticker"], rid) for p in peers}
            pos = con.execute("SELECT peer_median, position, unit FROM peer_position p LEFT JOIN (SELECT DISTINCT ratio_id r, unit FROM comparison_ratios) u ON u.r=p.ratio_id "
                              "WHERE ticker=? AND fiscal_year=? AND ratio_id=?", [ticker, fy, rid]).fetchone()
            if mine is None or all(v is None for v in theirs.values()):
                continue
            unit = (pos[2] if pos else None) or next((r[0] for r in con.execute("SELECT unit FROM comparison_ratios WHERE ratio_id=? LIMIT 1", [rid]).fetchall()), "x")
            rows.append({"ratio": rid, "label": labels.get(rid, rid.replace("_", " ")), "unit": unit, "company": mine, "peers": theirs,
                         "median": pos[0] if pos else None, "position": pos[1] if pos else None})
        if rows:
            groups[gname] = rows
    return {"fiscal_year": fy, "basis": "third-party data for every company, so definitions match; not audited figures",
            "peers": [{"ticker": p["ticker"], "name": p["name"]} for p in peers], "groups": groups}


def finding_record(f: dict) -> dict:
    """One finding as the JSON record the spec asks for (id, section, observation, numbers with sources, question, why, hypotheses, ...)."""
    written = f["status"] == "written"
    r = f.get("review")
    return {
        "id": f["id"], "section": f["section"], "title": f["title"],
        "observation": f.get("observation") if written else None,
        "why_it_matters": f.get("why_it_matters") if written else None,
        "evidence": f.get("evidence", []),
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
    auditor = json.loads((base / "auditor_report.json").read_text()) if (base / "auditor_report.json").exists() else None
    dupont = json.loads((base / "dupont.json").read_text()) if (base / "dupont.json").exists() else None
    from fsa.analyse import usd as usdmod
    usd = usdmod.usd_view(con, chart, ratio_rules, layout["usd_view"], ticker, fy)
    not_generated = {k: v for k, v in NOT_GENERATED.items() if ratings is None}
    return {
        "schema": "report.v1", "version": VERSION, "status": "draft", "generated_on": date.today().isoformat(),
        "ticker": ticker, "company": company["name"], "fiscal_year": fy, "amounts_unit": "N millions unless stated; nominal, Group",
        "disclaimer": "Educational analysis, not investment advice. Items are matters to examine, never accusations. No buy, sell or hold view is given.",
        "statements": scorecards(con, chart, ratio_rules, {"years_shown": layout["years_shown"], "scorecards": layout["statements"]}, ticker, fy),
        "ratio_groups": scorecards(con, chart, ratio_rules, {"years_shown": layout["years_shown"], "scorecards": layout["ratio_groups"]}, ticker, fy),
        "peers": peers_block(con, layout, ticker, fy),
        "not_generated": not_generated, "ratings": ratings, "auditor_report": auditor, "dupont": dupont, "usd_view": usd, "scorecards": scorecards(con, chart, ratio_rules, layout, ticker, fy),
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
    ar = rep.get("auditor_report")
    if ar:
        k = ar["key_audit_matters"]
        L += [f"- **Auditor ({ar['firm'] or 'firm not identified'}):** opinion {ar['opinion']['type']}"
              + (f" (p.{ar['opinion']['page']})" if ar["opinion"]["page"] else "")
              + f"; going-concern material uncertainty section: {'yes' if ar['going_concern']['material_uncertainty_section'] else 'none found'}; "
              + f"{len(ar['emphasis_of_matter'])} emphasis-of-matter and {len(ar['other_matter'])} other-matter paragraph(s); key audit matters: "
              + (", ".join(t["title"] for t in k["titles"]) if k["titles"] else ("section present" if k["section_present"] else "none found"))
              + ("" if ar.get("signature_found") else ". The report's signature block was not found in the PDF text, so its end may be incomplete") + ".", ""]
    for letter in "BCDEFGHIJK":
        L += [f"## {letter}. {titles[letter]}", ""]
        if letter in rep["scorecards"]:
            L += _table(rep["scorecards"][letter])
        if letter == "E" and rep.get("usd_view"):
            from fsa.analyse import usd as usdmod
            L += ["### USD view (information only)", ""] + usdmod.to_markdown(rep["usd_view"])
        if letter == "E" and rep.get("dupont"):
            from fsa.analyse import dupont as dp
            L += ["### Five-stage DuPont analysis of ROE", ""] + dp.to_markdown(rep["dupont"], rep["dupont"].get("commentary"))
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
        L += [f"- p.{e['page']} '{e['section']}': “{e['quote']}”" for e in f.get("evidence", [])]
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
