"""Turn fired trigger rules into finding records: code-built facts with their sources, plus the rule's own question and hypotheses.

No model is used here. A later step (the writer) may turn these records into prose, but cannot add a number or change a trigger.
Every account figure carries statement, page and printed label (project rule 4).
"""
import ast
import json

from fsa.analyse.ratios import NotMeaningful
import yaml

from fsa.findings.triggers import TriggerEvaluator, build_context
from fsa.warehouse.db import ROOT

FIELDS = ("id", "section", "title", "type", "question", "why", "hypotheses", "who_answers", "docs_needed")


def _sources(con, ticker: str, fy: int) -> dict:
    """account code -> the printed lines behind it (current year, group)."""
    out: dict = {}
    for code, statement, label, page, value, unit in con.execute("""SELECT m.code, p.statement, p.label, p.page, p.value, p.unit FROM mapping_final m
        JOIN pdf_statement_lines p ON p.ticker=m.ticker AND p.fiscal_year=m.fiscal_year AND p.statement=m.statement AND p.seq=m.seq
        WHERE m.ticker=? AND m.fiscal_year=? AND m.code IS NOT NULL AND p.entity='group' AND p.period_end=make_date(?,12,31)""", [ticker, fy, fy]).fetchall():
        item = {"statement": statement, "label": label or "(unlabelled subtotal)", "page": page, "value": value, "unit": unit}
        if item not in out.setdefault(code, []):                    # the same printed line can be mapped more than once
            out[code].append(item)
    return out


def build_records(con, chart, ratio_rules, rules, results, ticker: str, fy: int) -> list[dict]:
    cur, prior, now, known, extra, lookup = build_context(con, chart, ratio_rules, ticker, fy)
    src = _sources(con, ticker, fy)
    units = {r["id"]: r.get("unit") for r in ratio_rules["ratios"]} | {r["id"]: r.get("unit") for r in yaml.safe_load((ROOT / "rules" / "forensic.yaml").read_text())["ratios"]}
    meanings = {r["id"]: r.get("why") for r in ratio_rules["ratios"]} | {r["id"]: r.get("why") for r in yaml.safe_load((ROOT / "rules" / "forensic.yaml").read_text())["ratios"]}
    by_id = {r["id"]: r for r in rules}
    records = []
    for res in results:
        if res["state"] != "fired":
            continue
        rule = by_id[res["rule_id"]]
        ev = TriggerEvaluator(cur, prior, ratio_rules["metrics"], now, known, extra, lookup)
        facts = []
        for name in rule.get("facts", []):
            try:
                value = ev.ev(ast.parse(name, mode="eval").body)
                entry = {"name": name, "value": value}
            except NotMeaningful as e:
                entry = {"name": name, "value": None, "note": f"not available: {e}"}
            if units.get(name):
                entry["unit"] = units[name]
            if meanings.get(name):
                entry["meaning"] = meanings[name]
            if name in src:
                entry["source"] = [{k: s[k] for k in ("statement", "label", "page")} | {"printed_value": s["value"], "unit": s["unit"]} for s in src[name]]
            elif name in known:
                entry["source"] = "subtotal or total computed by code from the mapped lines"
            facts.append(entry)
        rec = {k: rule.get(k) for k in FIELDS}
        if res.get("evidence"):
            rec["evidence"] = res["evidence"]
        rec |= {"ticker": ticker, "fiscal_year": fy, "materiality": res["materiality"], "materiality_measure": res["measure"],
                "materiality_note": res["reason"], "trigger": rule.get("trigger") or "text rule: " + ", ".join(rule.get("search", [])), "facts": facts, "status": "candidate", "version": "0.1-draft",
                "disclaimer": "An item to examine, not an accusation; educational analysis, not investment advice."}
        records.append(rec)
    order = {"high": 0, "medium": 1, "low": 2, "unrated": 3}
    return sorted(records, key=lambda r: order.get(r["materiality"], 9))


def to_markdown(records: list[dict], ticker: str, fy: int) -> str:
    lines = [f"# Finding candidates: {ticker} FY{fy}", "", "> Items to examine, never accusations. Educational analysis, not investment advice. Each is a candidate until written, quote-checked and reviewed.", ""]
    for r in records:
        lines += [f"## {r['id']} · {r['title']} · {str(r['materiality']).upper()} · {r['type']}", "", f"**Trigger:** `{r['trigger']}`  "]
        if r["materiality_note"]:
            lines.append(f"**Materiality note:** {r['materiality_note']}  ")
        for e in r.get("evidence", []):
            lines.append(f"- evidence p.{e['page']} '{e['section']}': “{e['quote']}”")
        lines += ["", "**Facts**", ""]
        for f in r["facts"]:
            v = "n/a" if f["value"] is None else f"{f['value']:,.2f}"
            s = f.get("source")
            ref = "; ".join(f"{x['statement']} p.{x['page']} '{x['label']}'" for x in s) if isinstance(s, list) else (s or "")
            lines.append(f"- {f['name']}: {v}" + (f" — {ref}" if ref else "") + (f" ({f['note']})" if f.get("note") else ""))
        lines += ["", f"**Question:** {r['question']}", "", f"**Why I'm asking:** {r['why']}", "", "**Hypotheses**", ""]
        for h in r["hypotheses"]:
            lines.append(f"- {h['h']}  \n  confirmed by: {h['confirm']}; ruled out by: {h['rule_out']}")
        lines += ["", f"**Who can answer:** {r['who_answers']}. **Documents:** {', '.join(r.get('docs_needed') or [])}", ""]
    return "\n".join(lines)
