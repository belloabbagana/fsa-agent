"""Build the 'data notes and disclaimers' for a company-year: every discrepancy, restatement, gap and judgement,
in neutral language, each with where it comes from. The report prints these; nothing here is an accusation.

Every note is generated from stored facts (checks, reconciliation, mapping) so it cannot drift from the numbers.
Notes written by a person live in config/disclosure_notes.yaml.
"""
from datetime import date

import yaml

from fsa.extract.pdf_statements import norm_label
from fsa.map.chart import label_index
from fsa.map.finalize import signed_off
from fsa.map.validate_mapping import account_totals, check_subtotals, computed
from fsa.warehouse.db import ROOT

NOTES_PATH = ROOT / "config" / "disclosure_notes.yaml"
FACTOR_TO_MILLIONS = {"NGN_million": 1.0, "NGN_thousand": 0.001}


def _m(value, unit) -> str:
    v = value * FACTOR_TO_MILLIONS.get(unit, 1.0)
    return f"{'-' if v < 0 else ''}₦{abs(v):,.0f}m"


def _pages(con, ticker, fy) -> dict:
    return dict(con.execute("SELECT statement, min(page) FROM pdf_statement_lines WHERE ticker=? AND fiscal_year=? GROUP BY statement",
                            [ticker, fy]).fetchall())


def _doc(con, ticker, fy) -> str:
    r = con.execute("SELECT file_name FROM source_files WHERE ticker=? AND provider='annual_report' AND file_name LIKE ?",
                    [ticker, f"%FY{fy}%"]).fetchone()
    return r[0] if r else f"{ticker} FY{fy} annual report"


def _unit(con, ticker, fy) -> str:
    r = con.execute("SELECT unit FROM pdf_statement_lines WHERE ticker=? AND fiscal_year=? AND unit LIKE 'NGN_%' AND unit<>'NGN_per_share' LIMIT 1",
                    [ticker, fy]).fetchone()
    return r[0] if r else "NGN_million"


def _final_mapping(con, ticker, fy):
    rows = con.execute("SELECT statement, seq, code, basis, status, label, rule_code, model_code, note FROM mapping_final WHERE ticker=? AND fiscal_year=?",
                       [ticker, fy]).fetchall()
    values = {(s, q): v for s, q, v in con.execute(
        "SELECT statement, seq, value FROM pdf_statement_lines WHERE ticker=? AND fiscal_year=? AND entity='group' AND period_end=?",
        [ticker, fy, date(fy, 12, 31)]).fetchall()}
    return rows, values


def generate(con, chart: dict, ticker: str, fy: int, notes_path=NOTES_PATH) -> list[dict]:
    out: list[dict] = []
    unit = _unit(con, ticker, fy)
    pages = _pages(con, ticker, fy)
    doc = _doc(con, ticker, fy)
    accts = {a["code"]: a for a in chart["accounts"]}
    idx = label_index(chart)

    def clean(label: str) -> str:
        """The chart's account name for a printed label (so damaged PDF text never reaches the report)."""
        codes = sorted({c for _, c in idx.get(norm_label(label), set())})
        return accts[codes[0]]["name"] if len(codes) == 1 else label.replace(chr(0), "").replace("·", "")[:48]

    def add(category, severity, text, ref=None):
        out.append({"category": category, "severity": severity, "text": text, "source_ref": ref})

    unit_word = {"NGN_million": "₦ millions", "NGN_thousand": "₦ thousands (shown here in ₦ millions)"}.get(unit, unit)

    # ---- standard disclaimers (always) ----
    add("standard", "standard", "This is educational analysis, not investment advice. Items identified are matters to examine; they are not conclusions about the company's conduct or accounts, and no buy, sell or hold view is given.")
    add("standard", "standard", f"Source: {doc}, the company's audited annual report for FY{fy}. Figures are the Group's, in {unit_word}. Numbers were extracted from the PDF by code, and every subtotal was re-added by code; no figure was typed or estimated by a model. Statements start on page(s): " + ", ".join(f"{k.replace('_', ' ')} p.{v}" for k, v in sorted(pages.items())) + ".")
    add("standard", "standard", "Third-party market data (NGN Market) is used only to cross-check the audited figures and for peer comparison. It is not reproduced in this report; where it differs from the audited report, the audited report prevails.")

    # ---- restatements: this report's comparatives vs the previous report ----
    rows = con.execute("""SELECT statement, replace(check_name,'comparative:',''), expected, actual FROM integrity_checks
                          WHERE ticker=? AND fiscal_year=? AND check_name LIKE 'comparative:%' AND status='fail' AND entity='group'
                          ORDER BY abs(actual-expected) DESC""", [ticker, fy]).fetchall()
    if rows:
        seen, top = set(), []
        for st, lab, e, a in rows:                     # largest first, one entry per account name
            nm = clean(lab)
            if nm not in seen:
                seen.add(nm)
                top.append((nm, e, a))
        top = top[:8]
        parts = "; ".join(f"{nm}: {_m(e, unit)} then, {_m(a, unit)} now" for nm, e, a in top)
        add("restatement", "important",
            f"Comparatives changed. In the FY{fy} report, {len(rows)} Group line(s) for FY{fy - 1} differ, beyond rounding, from what the FY{fy - 1} report showed. "
            f"Largest: {parts}. The extracted statements do not state the reason; the notes to the FY{fy} report should be read.",
            f"FY{fy} report pages {', '.join(str(p) for p in sorted(set(pages.values())))}; compared with the FY{fy - 1} report")
    later = con.execute("""SELECT count(*) FROM integrity_checks WHERE ticker=? AND fiscal_year=? AND check_name LIKE 'comparative:%' AND status='fail' AND entity='group'""",
                        [ticker, fy + 1]).fetchone()[0]
    if later:
        add("restatement", "important",
            f"These figures were later restated. The FY{fy + 1} report shows {later} Group line(s) for FY{fy} that differ from this report's figures. "
            f"Third-party data providers may already show the restated numbers; the figures in this report are as originally audited.",
            f"FY{fy + 1} report, comparative column")

    # ---- the filing's own arithmetic ----
    for st, ent, pend, name, exp, act in con.execute("""SELECT statement, entity, period_end, check_name, expected, actual FROM integrity_checks
            WHERE ticker=? AND fiscal_year=? AND status='fail' AND check_name NOT LIKE 'comparative:%' AND actual IS NOT NULL""", [ticker, fy]).fetchall():
        add("filing_arithmetic", "important",
            f"As printed, the {st.replace('_', ' ')} does not add up for {ent} {pend}: the check '{name.replace('_', ' ')}' shows a printed total of {_m(exp, unit)} against parts adding to {_m(act, unit)} (difference {_m(act - exp, unit)}). "
            "This is how the filing prints it (for example a figure shown without its brackets); it is flagged, not corrected.",
            f"{st.replace('_', ' ')} p.{pages.get(st, '?')}")

    # ---- standardised layout versus printed subtotals ----
    final, values = _final_mapping(con, ticker, fy)
    if final:
        mapping = {(s, q): c for s, q, c, *_ in final if c}
        totals = account_totals(chart, mapping, values)
        for r in check_subtotals(chart, mapping, values):
            if r["status"] == "differs":
                expl = ""
                for code in ("PL_MONETARY_POSITION", "PL_FX_OPERATING", "PL_BIOLOGICAL_FV"):
                    if abs(abs(r["difference"]) - abs(totals.get(code, 0.0))) <= 2 and totals.get(code, 0.0):
                        expl = (f" The difference equals the company's '{accts[code]['name'].lower()}' ({_m(totals[code], unit)}), which the company presents "
                                f"inside this subtotal and the standardised layout places elsewhere (chart of accounts decision).")
                        break
                add("standardised_vs_printed", "note",
                    f"{r['name'].replace(' (standardised)', '')} as printed is {_m(r['printed'], unit)}; in the standardised layout it is {_m(r['computed'], unit)} (difference {_m(r['difference'], unit)}).{expl or ' The cause has not been identified; the statement should be examined.'}",
                    "chart of accounts v" + chart.get("version", "?"))

        # ---- cash on the cash flow statement versus the balance sheet ----
        cf = computed(chart, "CF_CASH_CLOSING", totals) if "CF_CASH_CLOSING" in accts else None
        closing_printed = next((values.get((s, q)) for s, q, c, *_ in final if c == "CF_CASH_CLOSING"), None)
        bs_cash, overdraft = totals.get("BS_CASH"), totals.get("BS_BANK_OVERDRAFT")
        if closing_printed is not None and bs_cash is not None and abs(closing_printed - bs_cash) > 2:
            d = bs_cash - closing_printed
            expl = (f" The difference equals the bank overdraft shown on the balance sheet ({_m(overdraft, unit)}), so the cash flow statement nets overdrafts against cash."
                    if overdraft and abs(d - overdraft) <= 2 else " The notes (cash and cash equivalents) should be read for the reconciliation.")
            add("cash_definition", "note",
                f"Cash at the end of the year is {_m(closing_printed, unit)} on the cash flow statement but {_m(bs_cash, unit)} on the balance sheet (difference {_m(d, unit)}).{expl} "
                "Net debt in this report uses balance-sheet cash.", f"cash flows p.{pages.get('cash_flows', '?')}; financial position p.{pages.get('financial_position', '?')}")

        # ---- accounting treatments the reader should know about ----
        op = next((values.get((s, q)) for s, q, c, *_ in final if c == "PL_OPERATING_PROFIT"), None)
        if totals.get("PL_MONETARY_POSITION"):
            add("basis", "note", f"The company applies inflation accounting (IAS 29): its net monetary position result is {_m(totals['PL_MONETARY_POSITION'], unit)}. The standardised layout shows it below operating profit.", f"profit or loss p.{pages.get('profit_or_loss', '?')}")
        if totals.get("PL_FX_OPERATING"):
            add("basis", "note", f"Foreign exchange gains or losses of {_m(totals['PL_FX_OPERATING'], unit)} are presented inside operating profit by the company. They are not operating performance in substance; the analyst-adjusted view moves them out.", f"profit or loss p.{pages.get('profit_or_loss', '?')}")
        if totals.get("PL_BIOLOGICAL_FV"):
            share = f" ({totals['PL_BIOLOGICAL_FV'] / op:.0%} of printed operating profit)" if op else ""
            add("basis", "note", f"Operating profit includes a non-cash fair-value gain on biological assets of {_m(totals['PL_BIOLOGICAL_FV'], unit)}{share}.", f"profit or loss p.{pages.get('profit_or_loss', '?')}")
        if any(c == "PL_EPS_BASIC" and "kobo" in (lbl or "").lower() for _, _, c, _, _, lbl, *_ in final):
            add("basis", "note", "Earnings per share is printed in kobo by this company; it is converted to naira (divided by 100) where compared with other companies.")

        # ---- how lines were mapped ----
        from collections import Counter
        basis = Counter(r[3] for r in final)
        n_open = sum(1 for r in final if r[4] == "open")
        models = [m[0] for m in con.execute("SELECT DISTINCT model_used FROM line_mappings WHERE ticker=? AND fiscal_year=? AND model_used IS NOT NULL", [ticker, fy]).fetchall()]
        txt = (f"Printed lines were mapped to the standard chart of accounts (v{chart.get('version', '?')}) by rules, with an independent second opinion from "
               f"{', '.join(models) if models else 'no model'} (the model that answered was read from the usage report). "
               f"Of {len(final)} lines: {basis.get('agreed', 0)} agreed by both, {basis.get('rule_only', 0)} by rule alone, {basis.get('rule_model_silent', 0)} by rule where the model gave no code, {basis.get('analyst_decision', 0)} settled by the analyst's decision, "
               f"{basis.get('model_accepted', 0)} unlabelled totals accepted because the sum reproduces them, {basis.get('intermediate_total', 0)} unlabelled running totals not used, {n_open} still open. Model proposals can differ between runs, so every decision is recorded.")
        so = signed_off(ticker, fy)
        if n_open:
            status = " Open lines may affect the figures; do not rely on them until they are settled."
        elif so:
            same = str(so.get("chart_version")) == str(chart.get("version"))
            status = (f" The mapping was signed off by the analyst ({so.get('approved_by')}) on {so.get('date')} against chart v{so.get('chart_version')}."
                      + ("" if same else f" The chart has since changed to v{chart.get('version')}: a new sign-off is needed."))
        else:
            status = " The mapping has not yet been signed off by the analyst."
        add("mapping", "important" if (n_open or not so) else "note", txt + status)

    # ---- blank cells and damaged text ----
    blanks = con.execute("SELECT count(*), count(DISTINCT label), count(*) FILTER (entity='group'), count(*) FILTER (entity='company') FROM pdf_statement_lines WHERE ticker=? AND fiscal_year=? AND value_flag='blank_in_filing'", [ticker, fy]).fetchone()
    if blanks[0]:
        add("blank_cells", "note", f"{blanks[0]} cell(s) across {blanks[1]} line(s) are left blank in the filing ({blanks[2]} in Group columns, {blanks[3]} in Company columns). A blank is treated as nothing printed, never as a reported zero; a printed dash is a reported nil.")
    dmg = con.execute("SELECT count(DISTINCT seq || statement) FROM pdf_statement_lines WHERE ticker=? AND fiscal_year=? AND (label LIKE '%·%' OR label LIKE '%' || chr(0) || '%')", [ticker, fy]).fetchone()[0]
    if dmg:
        add("pdf_quality", "note", f"The PDF's text layer is damaged on {dmg} statement line(s) (dropped letters from broken ligatures, stray spaces inside words or numbers). Numbers were read by their position on the page and each statement was re-added by code to confirm them.")

    # ---- third-party data comparison ----
    rec = con.execute("SELECT status, count(*) FROM reconciliation WHERE ticker=? AND fiscal_year=? GROUP BY status", [ticker, fy]).fetchall()
    if rec:
        counts = dict(rec)
        bits = [f"{counts.get('match', 0)} match the audited report"]
        if counts.get("match_restated_comparative"):
            bits.append(f"{counts['match_restated_comparative']} match the RESTATED comparative in the FY{fy + 1} report rather than the original")
        if counts.get("differs"):
            bits.append(f"{counts['differs']} differ")
        if counts.get("not_comparable"):
            bits.append(f"{counts['not_comparable']} are the provider's own calculations or have an unclear definition and cannot be compared")
        add("third_party", "note", f"NGN Market cross-check, FY{fy}: " + "; ".join(bits) + ".")
        for metric, reason in con.execute("SELECT provider_metric, pdf_label FROM reconciliation WHERE ticker=? AND fiscal_year=? AND status='differs'", [ticker, fy]).fetchall():
            add("third_party", "important", f"NGN Market's {metric} differs from the audited report ({reason}); the audited figure is used.")
        for metric, label in con.execute("SELECT provider_metric, pdf_label FROM reconciliation WHERE ticker=? AND fiscal_year=? AND status='not_comparable' AND pdf_label LIKE 'derived:%unclear%'", [ticker, fy]).fetchall():
            add("third_party", "note", f"NGN Market's {metric} could not be matched: {label.replace('derived: ', '')}.")
    gaps = con.execute("""SELECT min(fiscal_year), max(fiscal_year), list(DISTINCT fiscal_year ORDER BY fiscal_year) FROM statement_lines_current
                          WHERE ticker=? AND period_type='FY'""", [ticker]).fetchone()
    if gaps[0]:
        missing = [y for y in range(gaps[0], gaps[1] + 1) if y not in gaps[2]]
        if missing:
            add("third_party", "note", f"The third-party annual data has no year(s) {', '.join(str(y) for y in missing)} for this company, so long-run trends skip them.")
    rounded = con.execute("""SELECT count(*) FILTER (value % 1000000 = 0), count(*) FROM statement_lines_current
                             WHERE ticker=? AND period_type='FY' AND fiscal_year=? AND value IS NOT NULL AND unit='NGN' AND abs(value) >= 1000000""", [ticker, fy]).fetchone()
    if rounded[1] and unit == "NGN_thousand" and rounded[0] == rounded[1]:
        add("third_party", "note", "The third-party provider stores this company's figures rounded to whole millions of naira, while the audited report is in thousands; differences below ₦1m are treated as rounding.")

    # ---- notes written by a person ----
    notes = yaml.safe_load(notes_path.read_text()) if notes_path.exists() else {}
    for n in (notes.get("general") or []):
        add("analyst_note", "note", n)
    for n in (((notes.get("companies") or {}).get(ticker) or {}).get(f"FY{fy}") or []):
        add("analyst_note", "note", n)
    return out


def save(con, ticker: str, fy: int, items: list[dict]) -> None:
    con.execute("DELETE FROM report_disclosures WHERE ticker=? AND fiscal_year=?", [ticker, fy])
    for i, it in enumerate(items, start=1):
        con.execute("INSERT INTO report_disclosures (ticker, fiscal_year, seq, category, severity, text, source_ref) VALUES (?,?,?,?,?,?,?)",
                    [ticker, fy, i, it["category"], it["severity"], it["text"], it["source_ref"]])


def to_markdown(ticker: str, fy: int, items: list[dict]) -> str:
    order = {"important": 0, "note": 1, "standard": 2}
    lines = [f"## Data notes and disclaimers: {ticker} FY{fy}", ""]
    for sev, title in (("important", "Important: items to examine"), ("note", "Notes on the data and its preparation"), ("standard", "Standard disclaimers")):
        group = [i for i in items if i["severity"] == sev]
        if group:
            lines += [f"### {title}", ""]
            lines += [f"- {i['text']}" + (f" *(Source: {i['source_ref']})*" if i["source_ref"] else "") for i in group] + [""]
    return "\n".join(lines)
