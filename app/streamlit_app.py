"""FSA Agent dashboard. Analyst mode (local, everything) or viewer mode (read-only snapshot of the two core companies).

Run:  ./fsa app                 (analyst)      ./fsa app --mode viewer      (viewer)
Rule: the viewer never reads reports/, data/ or the warehouse. It reads app/snapshot/ only.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas as pd
import streamlit as st

from fsa.app import data as appdata
from fsa.report import assemble as asm
from fsa.report.disclosures import TAGS

MODE = os.environ.get("FSA_MODE", "analyst" if appdata.analyst_available() else "viewer")
if MODE == "analyst" and not appdata.analyst_available():
    MODE = "viewer"
ANALYST = MODE == "analyst"

# Colours: green is reassuring, amber and orange are things to examine, red is serious.
GREEN, LIGHT_GREEN, AMBER, ORANGE, RED, GREY = "#1e8e3e", "#4caf50", "#f2b200", "#e8710a", "#d93025", "#9aa0a6"
RATING_COLOUR = {
    "Strong": GREEN, "Healthy": LIGHT_GREEN, "Mixed": AMBER, "Weak": ORANGE, "Distressed": RED,
    "High": GREEN, "Good": LIGHT_GREEN, "Moderate": AMBER, "Low": ORANGE, "Questionable": RED,
    "Green": GREEN, "Yellow": AMBER, "Orange": ORANGE, "Red": RED,
}
LEVEL_COLOUR = {"high": RED, "medium": ORANGE, "low": AMBER}
LEVEL_NAME = {"high": "red", "medium": "orange", "low": "orange"}
SECTION_INFO = {
    "A": ("Auditor's report", "What the auditor said: opinion, going concern, emphasis paragraphs and key audit matters."),
    "B": ("Financial performance", "Revenue, margins and profit, and how they moved."),
    "C": ("Balance sheet", "Assets, debt, liquidity and working capital at the year end."),
    "D": ("Cash flow", "Whether profit turns into cash, and how cash is spent (capex, dividends)."),
    "E": ("Key ratios", "Ratios across profitability, efficiency, liquidity and leverage."),
    "F": ("Earnings quality", "How much of profit is backed by cash and comes from core trading."),
    "G": ("Forensic indicators", "Statistical screens (Beneish, Altman, growth gaps) and restated figures."),
    "H": ("Capital allocation", "Debt raised, acquisitions and how capital is deployed."),
    "I": ("Sector and macro", "Effects of the naira, inflation and translation of foreign operations."),
    "J": ("Historical trend", "The direction of key measures across the years."),
    "K": ("Peer comparison", "Where the company sits against its industry peers."),
}

st.set_page_config(page_title="FSA Agent", page_icon="📊", layout="wide")
st.markdown(
    """<style>
.block-container{padding-top:1.4rem;max-width:1250px} h1{font-size:1.7rem}
.small{font-size:.85rem;opacity:.8}
.tag{display:inline-block;border-radius:9px;padding:0 .45rem;font-size:.72rem;font-weight:600;margin-left:.35rem;vertical-align:middle}
.rate-box{border-radius:12px;padding:1rem 1.2rem;color:#fff}
.rate-box h3{margin:0 0 .2rem 0;font-size:1.5rem}
.rate-box ul{margin:.4rem 0 0 1rem;padding:0} .rate-box li{margin:.25rem 0}
.fcard{border:1px solid rgba(128,128,128,.3);border-left:6px solid var(--c);border-radius:8px;padding:.6rem .9rem;margin:.5rem 0}
</style>""",
    unsafe_allow_html=True,
)

reports = appdata.list_reports(MODE)
if not reports:
    st.error("No reports found. Build them with ./fsa report (analyst) or ./fsa export-viewer (viewer).")
    st.stop()

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.markdown("### 📊 FSA Agent")
    st.caption(("Analyst mode · full access" if ANALYST else "Viewer mode · read-only demo") + " · educational analysis, not investment advice")
    tickers = sorted({t for t, _ in reports})
    names = {c["ticker"]: c["name"] for c in appdata.companies_cfg()["companies"]}
    ticker = st.selectbox("Company", tickers, format_func=lambda t: f"{names.get(t, t)} ({t})")
    fy = st.selectbox("Fiscal year", sorted({y for t, y in reports if t == ticker}, reverse=True), format_func=lambda y: f"FY{y}")
    pages = ["Company", "Ratios", "Findings", "Peers", "Sources and data quality"] + (["Ask the agent"] if ANALYST else [])
    page = st.radio("Screen", pages, label_visibility="collapsed")
    st.divider()
    st.caption("Items to examine, never accusations. No buy, sell or hold view. Every number cites its source.")

rep = appdata.load_report(ticker, fy, MODE)
answers = appdata.load_answers(ticker, fy) if ANALYST else []
st.title(f"{rep['company']} · FY{fy}")


# ------------------------------------------------------------------ helpers
def money(v) -> str:
    return "n/a" if v is None else (f"({abs(v):,.0f})" if v < 0 else f"{v:,.0f}")


def show_rows(rows: list[dict]):
    """A static table (renders everywhere, prints well); the first column becomes the row label."""
    df = pd.DataFrame(rows)
    st.table(df.set_index(df.columns[0]))


def card_table(card: dict, fmt=None):
    rows = []
    for r in card["rows"]:
        row = {"Item": r["label"]}
        for y in card["years"]:
            c = r["values"][str(y)]
            if c["value"] is None:
                row[f"FY{y}"] = "N/M" if str(c.get("note", "")).startswith("N/M") else "n/a"
            else:
                row[f"FY{y}"] = fmt(c["value"], y) if fmt else asm.fmt(c["value"], r["unit"])
        rows.append(row)
    show_rows(rows)


def colour_box(title: str, colour: str, bullets: list[str], subtitle: str = ""):
    ink = "#222" if colour == AMBER else "#fff"
    items = "".join(f"<li>{b}</li>" for b in bullets if b)
    st.markdown(f"<div class='rate-box' style='background:{colour};color:{ink}'><span class='small'>{subtitle}</span>"
                f"<h3 style='color:{ink}'>{title}</h3><ul>{items}</ul></div>", unsafe_allow_html=True)


def lvl(word):
    return {"strong": "strong", "adequate": "adequate", "weak": "weak", "critical": "under pressure"}.get(word, "not rated")


def health_bullets(rt: dict) -> list[str]:
    p = rt["financial_health"]["pillars"]
    out = []
    v = p["profitability"]["values"]
    out.append(f"Profitability is {lvl(p['profitability']['level'])}"
               + (f": return on invested capital of {v['roic']:.1f}%" if "roic" in v else "")
               + (f" and an operating margin of {v['operating_margin']:.1f}%" if "operating_margin" in v else "") + ".")
    v, bits = p["liquidity"]["values"], []
    if "current_ratio" in v:
        bits.append(f"current assets cover current liabilities {v['current_ratio']:.2f} times")
    if "cfo_to_current_liabilities" in v:
        bits.append(f"a year's operating cash flow covers {v['cfo_to_current_liabilities']:.2f} times current liabilities")
    if "cash_ratio" in v:
        bits.append(f"cash alone covers {v['cash_ratio']:.2f} times")
    out.append(f"Liquidity is {lvl(p['liquidity']['level'])}" + (": " + ", ".join(bits) if bits else "") + ".")
    v, bits = p["leverage"]["values"], []
    if "interest_cover" in v:
        bits.append(f"operating profit covers interest {v['interest_cover']:.1f} times")
    if "net_debt_to_ebitda" in v:
        bits.append(f"net debt is {v['net_debt_to_ebitda']:.2f} times EBITDA")
    out.append(f"Leverage is {lvl(p['leverage']['level'])}" + (": " + " and ".join(bits) if bits else "") + ".")
    v, bits = p["cash_generation"]["values"], []
    if "FCF" in v:
        bits.append(f"free cash flow was ₦{v['FCF']:,.0f} million")
    if "cfo_to_net_income" in v:
        bits.append(f"operating cash flow was {v['cfo_to_net_income']:.2f} times profit")
    out.append(f"Cash generation is {lvl(p['cash_generation']['level'])}" + (": " + " and ".join(bits) if bits else "") + ".")
    return out


def earnings_bullets(rt: dict) -> list[str]:
    tests = {t["id"]: t for t in rt["earnings_quality"]["tests"]}
    bad = lambda k: tests.get(k, {}).get("result") == "adverse"
    val = lambda k, n: tests[k]["values"].get(n)
    out = []
    if bad("cash_backing"):
        out.append(f"Operating cash flow was {val('cash_backing', 'cfo_to_net_income'):.2f} times profit{' (well below one)' if bad('cash_backing_deep') else ''}, "
                   "so part of the profit has not turned into cash." + (" The same was true the year before." if bad("cash_backing_repeat") else ""))
    if bad("accruals"):
        out.append(f"Accruals were {val('accruals', 'accruals_ratio'):.1f}% of assets: profit is running ahead of cash.")
    if bad("non_core"):
        out.append(f"{abs(val('non_core', 'non_core_share_of_pretax_profit')):.1f}% of pre-tax profit came from items outside core trading, such as FX and fair-value gains.")
    if bad("restated"):
        out.append(f"{int(val('restated', 'restated_lines'))} prior-year figures were restated in this report.")
    if bad("growth_gaps"):
        n = int(val("growth_gaps", "flags_tripped"))
        out.append(f"{n} growth-gap screen{'s were' if n != 1 else ' was'} tripped (receivables, inventory or payables growing faster than sales or costs, or cash below profit).")
    if bad("monetary_position"):
        out.append(f"The inflation-accounting result was {abs(val('monetary_position', 'monetary_position_share_of_pretax_profit')):.1f}% of pre-tax profit.")
    return out or ["No earnings-quality test raised a concern: profit is backed by cash and comes mainly from core trading."]


def forensic_bullets(rt: dict) -> list[str]:
    v = rt["forensic_risk"].get("values", {})
    out = []
    if "beneish_m" in v:
        b = v["beneish_m"]
        out.append(f"Beneish M-score of {b:.2f}: " + ("above the −1.78 line, where scores look more like companies that later restated their accounts." if b > -1.78
                                                       else "below the −1.78 line, so this screen does not flag the accounts."))
    if "altman_z" in v:
        z = v["altman_z"]
        out.append(f"Altman Z'' score of {z:.2f}: " + ("below 4.35, the distress zone for emerging-market companies." if z < 4.35 else "above 4.35, outside the distress zone."))
    if "flags_tripped" in v:
        n = int(v["flags_tripped"])
        out.append(f"{n} of 4 growth-gap screens tripped." if n else "No growth-gap screen tripped.")
    if v.get("restated_lines"):
        out.append(f"{int(v['restated_lines'])} prior-year figures were restated.")
    if "auditor_adverse" in v:
        out.append("The auditor's opinion was unmodified, with no going-concern uncertainty." if v["auditor_adverse"] == 0
                   else "The auditor's report contains a modified opinion or a going-concern uncertainty.")
    meaning = {"Green": "No screen tripped. This never proves the accounts are free of risk.", "Yellow": "Some screens tripped: worth examining, not evidence of wrongdoing.",
               "Orange": "Several screens agree: these areas deserve close examination.", "Red": "The auditor's report or several strong signals point to serious concerns."}
    return out + [meaning.get(rt["forensic_risk"]["rating"], "")]


def quality_bullets(rt: dict) -> list[str]:
    i = rt["data_quality"].get("inputs") or {}
    out = []
    if "open_mapping_lines" in i:
        out.append("Every printed line is matched to the standard chart of accounts and signed off by the analyst." if not i["open_mapping_lines"] and not i.get("unsigned_mapping")
                   else "Some printed lines are not yet matched or signed off, so dependent figures need care.")
    if i.get("filing_arithmetic_notes"):
        out.append(f"The filing's own arithmetic fails in {int(i['filing_arithmetic_notes'])} place(s); the figures are flagged, not corrected.")
    if i.get("pdf_quality_notes"):
        out.append("Parts of the PDF text are damaged; the numbers were confirmed by re-adding every statement.")
    r = rt["forensic_risk"].get("values", {}).get("restated_lines")
    if r:
        out.append(f"{int(r)} prior-year figures were restated, so year-on-year comparisons need care.")
    if i.get("provider_differences") and ANALYST:
        out.append(f"{int(i['provider_differences'])} figures differ from the third-party cross-check; the audited figures are used.")
    return out or ["The figures are complete, mapped and reconcile to the filing."]


# ------------------------------------------------------------------ Company
if page == "Company":
    rt = rep.get("ratings")
    if rt:
        cards = [("health", "Financial health", rt["financial_health"]["rating"], health_bullets),
                 ("eq", "Earnings quality", rt["earnings_quality"]["rating"], earnings_bullets),
                 ("forensic", "Forensic risk", rt["forensic_risk"]["rating"], forensic_bullets),
                 ("dq", "Data quality", rt["data_quality"]["grade"], quality_bullets)]
        css = []
        for key, _, val, _ in cards:
            c = RATING_COLOUR.get(val, GREY)
            ink = "#222" if c == AMBER else "#fff"
            css.append(f".st-key-card_{key} button{{background:{c};color:{ink};border:none;border-radius:12px;min-height:4.6rem;width:100%}}"
                       f".st-key-card_{key} button p{{font-size:1.05rem;font-weight:600;color:{ink};white-space:pre-line;line-height:1.35}}"
                       f".st-key-card_{key} button:hover{{filter:brightness(1.08);color:{ink}}}")
        st.markdown("<style>" + "".join(css) + "</style>", unsafe_allow_html=True)
        open_card = st.session_state.get("open_card")

        def card_button(key, label, val, where):
            if where.button(f"{label}\n{val}", key=f"card_{key}", use_container_width=True):
                st.session_state["open_card"] = None if st.session_state.get("open_card") == key else key
                st.rerun()

        if open_card is None:
            for col, (key, label, val, _) in zip(st.columns(4), cards):
                card_button(key, label, val, col)
            st.caption("Click a rating to see what drove it.")
        else:
            left, right = st.columns([3, 1])
            for key, label, val, fn in cards:
                if key == open_card:
                    with left:
                        colour_box(val, RATING_COLOUR.get(val, GREY), fn(rt), subtitle=label)
                        if st.button("Close", key="close_card"):
                            st.session_state["open_card"] = None
                            st.rerun()
                else:
                    card_button(key, label, val, right)

    ar = rep.get("auditor_report")
    if ar:
        k = ar["key_audit_matters"]
        st.info(
            f"**Auditor's opinion ({ar['firm'] or 'firm not identified'}):** {ar['opinion']['type']}"
            + (f" (page {ar['opinion']['page']})" if ar["opinion"]["page"] else "")
            + f". Going-concern uncertainty: {'reported' if ar['going_concern']['material_uncertainty_section'] else 'none'}."
            + (f" Key audit matters: {', '.join(t['title'] for t in k['titles'])}." if k["titles"] else "")
        )

    st.subheader("Statements overview")
    u = rep.get("usd_view") or {}
    has_usd = u.get("status") == "ok"
    ccy = st.radio("Currency", ["NGN", "USD"] if has_usd else ["NGN"], horizontal=True, label_visibility="collapsed")
    rates = u.get("rates", {}) if has_usd else {}
    avg, close = rates.get("FX_USDNGN_OFFICIAL_AVG_ANNUAL", {}), rates.get("FX_USDNGN_OFFICIAL_CLOSE_YEAREND", {})
    st.caption("₦ millions, as printed; brackets are negative." if ccy == "NGN"
               else "US$ millions at the official rate: income statement and cash flow at each year's average rate, balance sheet at the year-end rate. Brackets are negative.")
    tabs = st.tabs(["Income statement", "Balance sheet", "Cash flow statement"])
    for tab, key, basis in zip(tabs, ("income_statement", "balance_sheet", "cash_flow"), (avg, close, avg)):
        with tab:
            card = rep["statements"][key]
            if ccy == "NGN":
                card_table(card, lambda v, y: money(v))
            else:
                card_table(card, lambda v, y, b=basis: money(v / b[str(y)]) if b.get(str(y)) else "n/a")
    if ccy == "USD":
        ys = rep["statements"]["income_statement"]["years"]
        st.markdown("**Official exchange rates (₦ per US$)**")
        show_rows([{"Rate": "Annual average", **{f"FY{y}": f"{avg[str(y)]:,.1f}" if avg.get(str(y)) else "n/a" for y in ys}},
                   {"Rate": "Year-end", **{f"FY{y}": f"{close[str(y)]:,.1f}" if close.get(str(y)) else "n/a" for y in ys}}])
        st.caption(f"Source: {u.get('source')}, vintage {u.get('vintage_id')}. Converted at the official rate; not adjusted for inflation.")

# ------------------------------------------------------------------ Ratios
elif page == "Ratios":
    for key, card in rep["ratio_groups"].items():
        st.subheader(card["title"])
        card_table(card)
    dp = rep.get("dupont")
    st.subheader("Five-stage DuPont analysis of ROE")
    if not dp:
        st.write("Not available for this report.")
    else:
        from fsa.analyse import dupont as dpm

        ys = dp["years"]
        rows = [{"Stage": name, **{f"FY{y['fiscal_year']}": dpm._f(k, y["legs"][k]) for y in ys}} for k, name, _ in dpm.STAGES + [dpm.OWNERS]]
        rows.append({"Stage": "Return on equity", **{f"FY{y['fiscal_year']}": f"{y['roe']:,.1f}%" for y in ys}})
        show_rows(rows)
        st.caption("ROE = tax burden × interest burden × operating margin × asset turnover × financial leverage (× owners' share of profit). "
                   "Financial leverage is average total assets divided by average owners' equity.")
        st.markdown("**What drove ROE each year**")
        first = ys[0]
        st.markdown(f"- FY{first['fiscal_year']}: ROE of {first['roe']:.1f}%, of which financial leverage adds {first['leverage_adds_pp']:.1f} percentage points "
                    f"(ROE before leverage {first['roe_before_leverage']:.1f}%).")
        for c in dp["changes"]:
            if c["status"] != "ok":
                st.markdown(f"- FY{c['fiscal_year']}: the split is not available ({c['reason']}).")
                continue
            now = next(y for y in ys if y["fiscal_year"] == c["fiscal_year"])
            top = [k for k in c["ranked"] if abs(c["contributions_pp"][k]) >= 0.5][:2]
            movers = " and ".join(f"{dpm.LABEL[k].lower()} ({c['contributions_pp'][k]:+.1f} pp)" for k in top) or "small moves across all stages"
            st.markdown(f"- FY{c['fiscal_year']}: ROE {'rose' if c['delta_roe_pp'] > 0 else 'fell'} {abs(c['delta_roe_pp']):.1f} percentage points to {now['roe']:.1f}%, mainly from {movers}.")
        words = dp.get("commentary")
        if words and words.get("status") == "written":
            with st.expander("Commentary"):
                st.markdown(words["summary"])
                st.caption(f"Drafted by {', '.join(words['written_by'])}; every number checked by code.")

# ------------------------------------------------------------------ Findings
elif page == "Findings":
    fs = rep["findings"]
    st.markdown(f"**{len(fs)} items to examine.** Each says what the figures show, why it matters, the question to ask, and the evidence that would confirm or rule out each explanation.")
    used_sections = set()
    for level, title in (("high", "High"), ("medium", "Medium"), ("low", "Low")):
        group = [f for f in fs if f["materiality"] == level]
        if not group:
            continue
        with st.expander(f":{LEVEL_NAME[level]}[**{title} materiality**] · {len(group)} item{'s' if len(group) != 1 else ''}", expanded=(level == "high")):
            for f in group:
                used_sections.add(f["section"])
                mine = [a for a in answers if a["finding_id"] == f["id"]]
                st.markdown(f"<div class='fcard' style='--c:{LEVEL_COLOUR[level]}'><b>{f['title']}</b>" + (" · ✅ answered" if mine else "") + "</div>", unsafe_allow_html=True)
                if f["wording_status"] == "written":
                    st.markdown(f["observation"])
                    st.markdown(f"**Why it matters.** {f['why_it_matters']}")
                else:
                    st.warning("The drafted wording failed the code checks; see the figures below.")
                st.markdown(f"**Question for {f['who_answers']}.** {f['question']}")
                if st.toggle("More detail", key=f"more_{f['id']}"):
                    st.markdown("**Possible explanations, and the evidence that would confirm or rule them out**")
                    for h in f["hypotheses"]:
                        st.markdown(f"- {h['h']}. Confirmed by {h['confirm']}; ruled out by {h['rule_out']}.")
                    r = f.get("review")
                    if r:
                        st.markdown(f"**Independent review:** {r['verdict'].replace('_', ' ')}.")
                        if ANALYST and r.get("notes_passed_checks"):
                            for e in r.get("overlooked_explanations", []):
                                st.markdown(f"- Another explanation to consider: {e}")
                    rows = []
                    for n in f["numbers"]:
                        src = n.get("sources")
                        where = "; ".join(f"{s['statement'].replace('_', ' ')} p.{s['page']}" for s in src) if isinstance(src, list) else (src or "")
                        rows.append({"Figure": n["name"].replace("_", " "), "Value": "n/a" if n["value"] is None else f"{n['value']:,.2f}", "Source": where})
                    rows += [{"Figure": "Quote", "Value": "", "Source": f"p.{e['page']}: “{e['quote']}”"} for e in f.get("evidence", [])]
                    if rows:
                        show_rows(rows)
                    if f.get("docs_needed"):
                        st.markdown(f"**Documents to request:** {', '.join(f['docs_needed'])}.")
                    if ANALYST:
                        for a in mine:
                            st.success(f"**Answer ({a['answered_by']}, {a['at'][:10]}, {a['version']}):** {a['answer']}" + (f"  \n_Source:_ {a['source_note']}" if a["source_note"] else ""))
                        with st.form(f"ans_{f['id']}", clear_on_submit=True):
                            txt = st.text_area("Record an answer to this question", key=f"t_{f['id']}", height=90)
                            default = appdata.ANSWER_WHO.index(f["who_answers"]) if f["who_answers"] in appdata.ANSWER_WHO else 0
                            who = st.selectbox("Who answered", appdata.ANSWER_WHO, index=default, key=f"w_{f['id']}")
                            src_note = st.text_input("Source of the answer (document, page, call date)", key=f"s_{f['id']}")
                            if st.form_submit_button("Save answer"):
                                try:
                                    rec = appdata.save_answer(ticker, fy, f["id"], txt, who, src_note)
                                    st.success(f"Saved as {rec['version']}. Earlier answers stay in the log.")
                                    st.rerun()
                                except ValueError as e:
                                    st.error(str(e))
                name = SECTION_INFO.get(f["section"], (f["section"], ""))[0]
                st.markdown(f"*Report section {f['section']}: {name}*")
                st.divider()
    st.markdown("#### Report section key")
    show_rows([{"Section": s, "Name": SECTION_INFO[s][0], "What it covers": SECTION_INFO[s][1]} for s in sorted(used_sections) if s in SECTION_INFO])

# ------------------------------------------------------------------ Peers
elif page == "Peers":
    pb = rep.get("peers")
    if not ANALYST or not pb:
        st.info("The peer comparison uses third-party market data and is not part of the public demo." if not ANALYST else "No peer comparison is available for this company-year.")
    else:
        st.markdown(f"{rep['company']} against {', '.join(p['name'] for p in pb['peers'])}. All figures use the same third-party data for every company, "
                    "so definitions match; they are not audited figures. Only comparisons with figures on both sides are shown.")

        def pf(v, unit):
            if v is None:
                return "–"
            return {"pct": f"{v:,.1f}%", "days": f"{v:,.0f}", "x": f"{v:,.2f}x"}.get(unit, f"{v:,.2f}")

        for gname, rows in pb["groups"].items():
            st.subheader(gname)
            show_rows([{"Ratio": r["label"], ticker: pf(r["company"], r["unit"]), **{t: pf(v, r["unit"]) for t, v in r["peers"].items()},
                        "Peer median": pf(r["median"], r["unit"]), "Position": (r["position"] or "").replace("_", " ")} for r in rows])
        st.caption("Position says where the company sits against the peer median (above, near or below), not whether that is good or bad. – means the peer's figure is not available.")

# ------------------------------------------------------------------ Sources and data quality
elif page == "Sources and data quality":
    ar = rep.get("auditor_report")
    if ar:
        st.subheader("Auditor's report")
        st.markdown(f"Read by code from pages {ar['pages'][0]}–{ar['pages'][1]} of the filing. Auditor: {ar['firm'] or 'not identified'}. Opinion: {ar['opinion']['type']}. "
                    "Every quote below was checked against the PDF text.")
        if ar["opinion"].get("quote"):
            st.markdown(f"> {ar['opinion']['quote']}  \n> — page {ar['opinion']['page']}")
        for e in ar["emphasis_of_matter"] + ar["other_matter"]:
            st.markdown(f"> {e['quote']}  \n> — page {e['page']}, {e['section']}")
        k = ar["key_audit_matters"]
        if k["titles"]:
            st.markdown("**Key audit matters:** " + "; ".join(f"{t['title']} (page {t['page']})" for t in k["titles"]) + ".")
        if not ar.get("signature_found"):
            st.warning("The report's signature block was not found in the PDF text, so its end may be incomplete.")

    st.subheader("Data notes and disclaimers")
    order = {"important": 0, "note": 1, "standard": 2}
    used = []
    for n in sorted(rep["data_notes"], key=lambda n: order.get(n["severity"], 3)):
        tag = n.get("tag") or TAGS.get(n["category"], (n["category"],))[0]
        colour = RED if n["severity"] == "important" else GREY
        st.markdown(f"- {n['text']} <span class='tag' style='background:{colour};color:#fff'>{tag}</span>", unsafe_allow_html=True)
        if n["category"] not in used:
            used.append(n["category"])
    st.markdown("**Tag key** · red tags need attention, grey tags are background.")
    show_rows([{"Tag": TAGS[c][0], "What it covers": TAGS[c][1]} for c in used if c in TAGS])

    u = rep.get("usd_view")
    if u and u.get("status") == "ok":
        st.subheader("Macro data")
        st.markdown(f"Exchange rates: {u['source']}. Vintage {u['vintage_id']}, retrieved {u['retrieved_at'][:10]}, file fingerprint `{u['raw_file_sha256'][:16]}…`"
                    + (". **This vintage is older than 90 days.**" if u["vintage_older_than_limit"] else "."))

    st.subheader("Source documents")
    srcs = rep.get("sources") or (appdata.sources_for(ticker, fy) if ANALYST else [])
    for s in srcs:
        st.markdown(f"- {s.get('description', 'Annual report')}: [link to the filing]({s.get('url')}), downloaded {s.get('downloaded')}, fingerprint `{(s.get('sha256') or '')[:16]}…`")
    st.caption("Filings are linked, never copied. Every number in the analysis carries its statement and page.")

# ------------------------------------------------------------------ Ask the agent (analyst only)
elif page == "Ask the agent":
    from fsa.app import ask as askmod

    st.markdown(
        f"Ask about **{rep['company']} FY{fy}**. The agent answers only from this report, says where the answer comes from, and says so when something is not in it. "
        "Every number in its answer is checked against the report by code. Model: Haiku (low cost)."
    )
    hist = st.session_state.setdefault("ask_history", {}).setdefault((ticker, fy), [])
    for q, a in hist:
        st.chat_message("user").write(q)
        st.chat_message("assistant").write(a)
    q = st.chat_input("Ask a question about this report")
    if q:
        st.chat_message("user").write(q)
        with st.chat_message("assistant"):
            with st.spinner("Reading the report…"):
                try:
                    res = askmod.answer_question(rep, q)
                except Exception as e:  # a failed model call is shown, never hidden
                    res = {"status": "error", "answer": None, "problems": [str(e)], "cost_usd": 0.0, "model": []}
            if res["status"] == "answered":
                msg = res["answer"] + f"\n\n_{', '.join(res['model'])} · ${res['cost_usd']:.3f} · numbers checked by code_"
            else:
                msg = "I could not give an answer that passes the checks (" + "; ".join(res.get("problems", [])) + "). Please rephrase, or read the report sections directly."
            st.write(msg)
        hist.append((q, msg))
