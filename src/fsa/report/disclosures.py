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
# Each note ends with a tag; the report prints this key under the notes.
TAGS = {
    "standard": ("Disclaimer", "How to read this analysis and where the figures come from."),
    "restatement": ("Restatement", "Prior-year figures that changed between one annual report and the next."),
    "filing_arithmetic": ("Filing arithmetic", "A total in the filing that does not equal the sum of its printed lines."),
    "standardised_vs_printed": ("Layout difference", "A subtotal that differs between the company's layout and the standard layout used here."),
    "cash_definition": ("Cash definition", "Differences in what counts as cash between statements."),
    "basis": ("Accounting basis", "Accounting choices or events that affect how the figures should be read."),
    "mapping": ("Mapping", "How printed lines were matched to the standard chart of accounts, and who approved it."),
    "forensic": ("Forensic caveat", "Limits of the statistical screens used in the forensic section."),
    "blank_cells": ("Blank cells", "Cells left blank in the filing; never read as zero."),
    "pdf_quality": ("PDF quality", "Damaged text in the PDF and how the numbers were still confirmed."),
    "third_party": ("Third-party data", "The cross-check against third-party market data (used to check and to compare, never to replace audited figures)."),
    "peers": ("Peers", "How the peer comparison was built and its limits."),
    "analyst_note": ("Analyst note", "A note written by the analyst."),
}
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
        out.append({"category": category, "severity": severity, "text": text, "source_ref": ref, "tag": TAGS.get(category, (category, ""))[0]})

    unit_word = {"NGN_million": "₦ millions", "NGN_thousand": "₦ thousands (shown here in ₦ millions)"}.get(unit, unit)

    # ---- standard disclaimers (always) ----
    add("standard", "standard", "Educational analysis, not investment advice. Findings are items to examine, not conclusions, and no buy, sell or hold view is given.")
    names = {"profit_or_loss": "income statement", "financial_position": "balance sheet", "cash_flows": "cash flow statement", "comprehensive_income": "comprehensive income"}
    add("standard", "standard", f"Figures are the Group's, from the audited FY{fy} report ({unit_word}). Code extracted every number and re-added every subtotal; no figure was typed or estimated by a model. "
        + "Statements: " + ", ".join(f"{names.get(k, k.replace('_', ' '))} p.{v}" for k, v in sorted(pages.items(), key=lambda kv: kv[1])) + ".")
    add("standard", "standard", "Third-party market data is used only to cross-check the audited figures and to compare peers. Where it differs, the audited figure is used.")

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
        top = top[:3]
        parts = "; ".join(f"{nm} {_m(e, unit)} to {_m(a, unit)}" for nm, e, a in top)
        add("restatement", "important",
            f"{len(rows)} FY{fy - 1} figures were changed in this report's comparatives. Largest: {parts}. The statements do not give the reason.",
            f"FY{fy} report pages {', '.join(str(p) for p in sorted(set(pages.values())))}; compared with the FY{fy - 1} report")
    later = con.execute("""SELECT count(*) FROM integrity_checks WHERE ticker=? AND fiscal_year=? AND check_name LIKE 'comparative:%' AND status='fail' AND entity='group'""",
                        [ticker, fy + 1]).fetchone()[0]
    if later:
        add("restatement", "important",
            f"{later} of these figures were changed later, in the FY{fy + 1} report. This report shows them as originally audited.",
            f"FY{fy + 1} report, comparative column")

    # ---- the filing's own arithmetic ----
    for st, ent, pend, name, exp, act in con.execute("""SELECT statement, entity, period_end, check_name, expected, actual FROM integrity_checks
            WHERE ticker=? AND fiscal_year=? AND status='fail' AND check_name NOT LIKE 'comparative:%' AND actual IS NOT NULL""", [ticker, fy]).fetchall():
        add("filing_arithmetic", "important",
            f"The printed {st.replace('_', ' ')} ({ent}, {pend}) does not add up: the total is {_m(exp, unit)} but its lines add to {_m(act, unit)} (difference {_m(act - exp, unit)}). Flagged, not corrected.",
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
                        expl = f" The difference is the {accts[code]['name'].lower()}, which the company includes here and the standard layout shows separately."
                        break
                add("standardised_vs_printed", "note",
                    f"{r['name'].replace(' (standardised)', '')} is {_m(r['printed'], unit)} as printed and {_m(r['computed'], unit)} in the standard layout.{expl or ' The cause has not been identified.'}",
                    "chart of accounts v" + chart.get("version", "?"))

        # ---- cash on the cash flow statement versus the balance sheet ----
        cf = computed(chart, "CF_CASH_CLOSING", totals) if "CF_CASH_CLOSING" in accts else None
        closing_printed = next((values.get((s, q)) for s, q, c, *_ in final if c == "CF_CASH_CLOSING"), None)
        bs_cash, overdraft = totals.get("BS_CASH"), totals.get("BS_BANK_OVERDRAFT")
        if closing_printed is not None and bs_cash is not None and abs(closing_printed - bs_cash) > 2:
            d = bs_cash - closing_printed
            expl = (" The difference is the bank overdraft, which the cash flow statement nets against cash."
                    if overdraft and abs(d - overdraft) <= 2 else " The difference is not explained in the statements.")
            add("cash_definition", "note",
                f"Year-end cash is {_m(closing_printed, unit)} in the cash flow statement and {_m(bs_cash, unit)} on the balance sheet.{expl} Net debt here uses balance-sheet cash.", f"cash flows p.{pages.get('cash_flows', '?')}; financial position p.{pages.get('financial_position', '?')}")

        # ---- accounting treatments the reader should know about ----
        op = next((values.get((s, q)) for s, q, c, *_ in final if c == "PL_OPERATING_PROFIT"), None)
        if totals.get("PL_MONETARY_POSITION"):
            add("basis", "note", f"The company applies inflation accounting (IAS 29); its net monetary position result of {_m(totals['PL_MONETARY_POSITION'], unit)} is shown below operating profit here.", f"profit or loss p.{pages.get('profit_or_loss', '?')}")
        if totals.get("PL_FX_OPERATING"):
            add("basis", "note", f"Operating profit includes foreign exchange gains or losses of {_m(totals['PL_FX_OPERATING'], unit)}, as the company presents it. They are not trading performance.", f"profit or loss p.{pages.get('profit_or_loss', '?')}")
        if totals.get("PL_BIOLOGICAL_FV"):
            share = f" ({totals['PL_BIOLOGICAL_FV'] / op:.0%} of printed operating profit)" if op else ""
            add("basis", "note", f"Operating profit includes a non-cash fair-value gain on biological assets of {_m(totals['PL_BIOLOGICAL_FV'], unit)}{share}.", f"profit or loss p.{pages.get('profit_or_loss', '?')}")
        if any(c == "PL_EPS_BASIC" and "kobo" in (lbl or "").lower() for _, _, c, _, _, lbl, *_ in final):
            add("basis", "note", "Earnings per share is printed in kobo; it is divided by 100 to compare in naira.")

        if totals.get("CF_ACQUISITIONS"):
            add("basis", "important", f"A subsidiary was acquired in the year (net cash {_m(totals['CF_ACQUISITIONS'], unit)}), so this year's growth rates, working-capital changes and forensic scores mix organic and acquired amounts.", f"cash flows p.{pages.get('cash_flows', '?')}")

        # ---- how lines were mapped ----
        from collections import Counter
        basis = Counter(r[3] for r in final)
        n_open = sum(1 for r in final if r[4] == "open")
        models = [m[0] for m in con.execute("SELECT DISTINCT model_used FROM line_mappings WHERE ticker=? AND fiscal_year=? AND model_used IS NOT NULL", [ticker, fy]).fetchall()]
        settled = len(final) - n_open
        txt = (f"{settled} of {len(final)} printed lines were matched to the standard chart of accounts: {basis.get('agreed', 0)} where rules and an independent model agreed, "
               f"{basis.get('analyst_decision', 0)} by the analyst's decision, and the rest by rule or by totals that re-add exactly." + (f" {n_open} are still open." if n_open else ""))
        so = signed_off(ticker, fy)
        if n_open:
            status = " Figures that depend on open lines should not be relied on yet."
        elif so:
            same = str(so.get("chart_version")) == str(chart.get("version"))
            status = (f" Signed off by the analyst on {so.get('date')}." + ("" if same else f" The chart has since changed, so a new sign-off is needed."))
        else:
            status = " Not yet signed off by the analyst."
        add("mapping", "important" if (n_open or not so) else "note", txt + status)

    # ---- forensic indicators ----
    fcount = con.execute("SELECT count(*) FROM forensic_scores WHERE ticker=? AND fiscal_year=? AND status='ok'", [ticker, fy]).fetchone()[0]
    if fcount:
        add("forensic", "important", "The forensic scores (Beneish M-score, Altman Z'' and growth-gap tests) are statistical screens built on other markets. "
            "Devaluation, inflation and acquisitions move their inputs, so they are read only alongside the findings, never alone.")

    # ---- blank cells and damaged text ----
    blanks = con.execute("SELECT count(*), count(DISTINCT label), count(*) FILTER (entity='group'), count(*) FILTER (entity='company') FROM pdf_statement_lines WHERE ticker=? AND fiscal_year=? AND value_flag='blank_in_filing'", [ticker, fy]).fetchone()
    if blanks[0]:
        add("blank_cells", "note", f"{blanks[0]} cell(s) on {blanks[1]} line(s) are blank in the filing. A blank is read as not printed, never as zero; a printed dash is a reported nil.")
    dmg = con.execute("SELECT count(DISTINCT seq || statement) FROM pdf_statement_lines WHERE ticker=? AND fiscal_year=? AND (label LIKE '%·%' OR label LIKE '%' || chr(0) || '%')", [ticker, fy]).fetchone()[0]
    if dmg:
        add("pdf_quality", "note", f"The PDF text is damaged on {dmg} statement line(s) (lost letters, stray spaces). The numbers were read by position and every statement was re-added by code to confirm them.")

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
            bits.append(f"{counts['not_comparable']} cannot be compared (the provider's own calculations or unclear definitions)")
        add("third_party", "note", f"Cross-check with third-party data: " + "; ".join(bits) + ".")
        for metric, reason in con.execute("SELECT provider_metric, pdf_label FROM reconciliation WHERE ticker=? AND fiscal_year=? AND status='differs'", [ticker, fy]).fetchall():
            add("third_party", "important", f"The third-party {metric} differs from the audited figure ({reason}); the audited figure is used.")
        for metric, label in con.execute("SELECT provider_metric, pdf_label FROM reconciliation WHERE ticker=? AND fiscal_year=? AND status='not_comparable' AND pdf_label LIKE 'derived:%unclear%'", [ticker, fy]).fetchall():
            add("third_party", "note", f"The third-party {metric} has an unclear definition and could not be matched.")
    gaps = con.execute("""SELECT min(fiscal_year), max(fiscal_year), list(DISTINCT fiscal_year ORDER BY fiscal_year) FROM statement_lines_current
                          WHERE ticker=? AND period_type='FY'""", [ticker]).fetchone()
    if gaps[0]:
        missing = [y for y in range(gaps[0], gaps[1] + 1) if y not in gaps[2]]
        if missing:
            add("third_party", "note", f"Third-party history has no data for {', '.join(str(y) for y in missing)}, so long-run trends skip those years.")
    rounded = con.execute("""SELECT count(*) FILTER (value % 1000000 = 0), count(*) FROM statement_lines_current
                             WHERE ticker=? AND period_type='FY' AND fiscal_year=? AND value IS NOT NULL AND unit='NGN' AND abs(value) >= 1000000""", [ticker, fy]).fetchone()
    if rounded[1] and unit == "NGN_thousand" and rounded[0] == rounded[1]:
        add("third_party", "note", "Third-party figures are rounded to ₦1m, so smaller differences are treated as rounding.")

    # ---- the peer comparison ----
    pp = con.execute("SELECT count(*), max(peer_n), bool_or(small_set) FROM peer_position WHERE ticker=? AND fiscal_year=? AND position NOT IN ('not meaningful')", [ticker, fy]).fetchone()
    if pp and pp[0]:
        ind = con.execute("SELECT gics_industry FROM companies WHERE ticker=?", [ticker]).fetchone()
        peers = [r for r in con.execute("SELECT ticker, conflict_check FROM companies WHERE gics_industry=? AND ticker<>?", [ind[0], ticker]).fetchall()] if ind and ind[0] else []
        add("peers", "note", f"Peers: {len(peers)} companies in {ind[0] if ind else 'the industry'}, all compared on the same third-party data, which is not audited. Above, near or below says where a figure sits, not whether it is good or bad.")
        if pp[2]:
            add("peers", "note", f"With at most {pp[1]} peers, medians are indicative only.")
        cfg = {c["ticker"]: c.get("conflict_check") for c in yaml.safe_load((ROOT / "config" / "companies.yaml").read_text())["companies"]}
        unchecked = [t for t, _ in peers if cfg.get(t) != "pass"]          # config/companies.yaml is the record of the analyst's conflict checks
        if unchecked:
            add("peers", "important", f"{len(unchecked)} peer(s) have no conflict check recorded, so peer figures stay internal.")
        for t, _ in peers:
            ok = con.execute("SELECT count(*) FILTER (status='ok'), count(*) FROM comparison_ratios WHERE ticker=? AND fiscal_year=?", [t, fy]).fetchone()
            if ok[1] == 0:
                add("peers", "note", f"One peer has no third-party data for FY{fy} and is left out.")
            elif ok[0] < 0.5 * max(1, con.execute("SELECT count(*) FILTER (status='ok') FROM comparison_ratios WHERE ticker=? AND fiscal_year=?", [ticker, fy]).fetchone()[0]):
                add("peers", "note", f"One peer's FY{fy} data is mostly blank ({ok[0]} of {ok[1]} ratios), so it adds little.")

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
    """Bullets, the ones needing attention first, each ending with its tag; then a key that explains each tag used."""
    order = {"important": 0, "note": 1, "standard": 2}
    lines = [f"## Data notes and disclaimers: {ticker} FY{fy}", ""]
    for i in sorted(items, key=lambda i: order.get(i["severity"], 3)):
        lines.append(f"- {i['text']} `{i.get('tag') or TAGS.get(i['category'], (i['category'],))[0]}`" + (" (needs attention)" if i["severity"] == "important" else ""))
    used = sorted({i["category"] for i in items}, key=lambda c: list(TAGS).index(c) if c in TAGS else 99)
    lines += ["", "| Tag | What it covers |", "|---|---|"] + [f"| {TAGS[c][0]} | {TAGS[c][1]} |" for c in used if c in TAGS]
    return "\n".join(lines) + "\n"
