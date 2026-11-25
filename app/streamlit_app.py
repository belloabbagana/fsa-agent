"""FSA Agent dashboard. Analyst mode (local, everything) or viewer mode (read-only snapshot of the two core companies).

Run:  ./fsa app                 (analyst)      ./fsa app --mode viewer      (viewer)
Rule: the viewer never reads reports/, data/ or the warehouse. It reads app/snapshot/ only.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import streamlit as st

from fsa.app import data as appdata
from fsa.report import assemble as asm

MODE = os.environ.get("FSA_MODE", "analyst" if appdata.analyst_available() else "viewer")
if MODE == "analyst" and not appdata.analyst_available():
    MODE = "viewer"
ANALYST = MODE == "analyst"
LEVEL_ICON = {"high": "🔴", "medium": "🟠", "low": "🟡", "unrated": "⚪"}

st.set_page_config(page_title="FSA Agent", page_icon="📊", layout="wide")
st.markdown(
    """<style>.block-container{padding-top:1.4rem;max-width:1250px} h1{font-size:1.7rem} .small{font-size:.85rem;opacity:.7}
.card{border:1px solid rgba(128,128,128,.35);border-radius:10px;padding:.7rem .9rem;height:100%} .card b{font-size:1.25rem}</style>""",
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
    pages = ["Company", "Findings", "Peers", "Sources and data quality"] + (["Ask the agent"] if ANALYST else [])
    page = st.radio("Screen", pages, label_visibility="collapsed")
    st.divider()
    st.caption("Items to examine, never accusations. No buy, sell or hold view. Every number cites its source.")

rep = appdata.load_report(ticker, fy, MODE)
answers = appdata.load_answers(ticker, fy) if ANALYST else []
st.title(f"{rep['company']} · FY{fy}")
st.caption(f"Draft {rep['version']} · {rep['amounts_unit']} · generated {rep['generated_on']}")


def show_rows(rows: list[dict]):
    """A static table (renders everywhere, prints well); the first column becomes the row label."""
    import pandas as pd

    df = pd.DataFrame(rows)
    st.table(df.set_index(df.columns[0]))


def table(card: dict):
    ys = card["years"]
    rows = []
    for r in card["rows"]:
        row = {"Metric": r["label"]}
        for y in ys:
            c = r["values"][str(y)]
            row[f"FY{y}"] = "N/M" if str(c.get("note", "")).startswith("N/M") else ("n/a" if c["value"] is None else asm.fmt(c["value"], r["unit"]))
        rows.append(row)
    show_rows(rows)


# ------------------------------------------------------------------ Company
if page == "Company":
    rt = rep.get("ratings")
    if rt:
        cols = st.columns(4)
        cards = [("Financial health", rt["financial_health"]["rating"]), ("Earnings quality", rt["earnings_quality"]["rating"]),
                 ("Forensic risk", rt["forensic_risk"]["rating"]), ("Data quality", rt["data_quality"]["grade"])]
        for col, (label, val) in zip(cols, cards):
            col.markdown(f"<div class='card'><span class='small'>{label}</span><br><b>{val}</b></div>", unsafe_allow_html=True)
        st.caption("The three ratings are kept separate on purpose. They are counts of named tests from the rating rules, not verdicts. " + (rt["forensic_risk"]["note"] or ""))
        with st.expander("What drove the ratings"):
            st.markdown("**Financial health pillars:** " + ", ".join(f"{k.replace('_', ' ')} {v['level'] or 'not rated'}" for k, v in rt["financial_health"]["pillars"].items()))
            adverse = [t for t in rt["earnings_quality"]["tests"] if t["result"] == "adverse"]
            st.markdown(f"**Earnings quality:** {rt['earnings_quality']['points']} demerit point(s) from {rt['earnings_quality']['tests_run']} tests")
            for t in adverse:
                st.markdown(f"- {t['why']} (+{t['points']})")
            cn = rt["conclusion"]
            st.markdown(f"**Conclusion category (draft):** {cn['category']}. " + "; ".join(f"{x['measure'].replace('_', ' ')} {x['state']}" for x in cn["trend"]))
    ar = rep.get("auditor_report")
    if ar:
        k = ar["key_audit_matters"]
        st.info(
            f"**Auditor ({ar['firm'] or 'not identified'}):** opinion {ar['opinion']['type']}"
            + (f" (p.{ar['opinion']['page']})" if ar["opinion"]["page"] else "")
            + f" · going-concern uncertainty section: {'yes' if ar['going_concern']['material_uncertainty_section'] else 'none found'}"
            + (f" · key audit matters: {', '.join(t['title'] for t in k['titles'])}" if k["titles"] else "")
        )
    tabs = st.tabs(["Performance (B)", "Balance sheet (C)", "Cash flow (D)", "Ratios (E)", "DuPont on ROE", "USD view"])
    for tab, letter in zip(tabs[:4], "BCDE"):
        with tab:
            st.subheader(rep["scorecards"][letter]["title"])
            table(rep["scorecards"][letter])
    with tabs[4]:
        dp = rep.get("dupont")
        if not dp:
            st.write("DuPont analysis is not available for this report.")
        else:
            from fsa.analyse import dupont as dpm

            st.markdown("\n".join(dpm.to_markdown(dp, dp.get("commentary"))))
    with tabs[5]:
        from fsa.analyse import usd as usdm

        u = rep.get("usd_view")
        st.markdown("\n".join(usdm.to_markdown(u)) if u else "USD view not available.")

# ------------------------------------------------------------------ Findings
elif page == "Findings":
    fs = rep["findings"]
    st.markdown(f"**{len(fs)} items to examine.** Ranked by materiality. Each states what the figures show, why it matters, the question to ask and the evidence that would confirm or rule out each hypothesis.")
    c1, c2, c3 = st.columns(3)
    lv = c1.multiselect("Materiality", ["high", "medium", "low"], default=["high", "medium"])
    secs = sorted({f["section"] for f in fs})
    sec = c2.multiselect("Report section", secs, default=secs)
    types = sorted({f["type"] for f in fs})
    typ = c3.multiselect("Type", types, default=types)
    shown = [f for f in fs if f["materiality"] in lv and f["section"] in sec and f["type"] in typ]
    st.caption(f"{len(shown)} shown")
    for f in shown:
        mine = [a for a in answers if a["finding_id"] == f["id"]]
        tag = f" · ✅ answered ({len(mine)})" if mine else ""
        with st.expander(f"{LEVEL_ICON.get(f['materiality'], '')} {f['id']} · {f['title']} · {f['materiality'].upper()} · {f['type']}{tag}"):
            if f["wording_status"] == "written":
                st.markdown(f"**What the figures show.** {f['observation']}")
                st.markdown(f"**Why it matters.** {f['why_it_matters']}")
            else:
                st.warning("The drafted wording failed the code checks; use the figures below.")
            r = f.get("review")
            if r:
                st.markdown(f"**Independent review ({', '.join(r['reviewed_by'])}): {r['verdict'].replace('_', ' ')}.**")
                if ANALYST and r.get("notes_passed_checks"):
                    for p in r.get("problems", []):
                        st.markdown(f"- Problem: {p}")
                    for e in r.get("overlooked_explanations", []):
                        st.markdown(f"- Possible explanation not yet listed: {e}")
            st.markdown(f"**Question ({f['who_answers']}).** {f['question']}")
            st.markdown(f"**Why I'm asking.** {f['why_i_am_asking']}")
            st.markdown("**Hypotheses and evidence**")
            for h in f["hypotheses"]:
                st.markdown(f"- {h['h']}  \n  _confirmed by:_ {h['confirm']} · _ruled out by:_ {h['rule_out']}")
            rows = []
            for n in f["numbers"]:
                src = n.get("sources")
                where = "; ".join(f"{s['statement'].replace('_', ' ')} p.{s['page']} '{s['label']}'" for s in src) if isinstance(src, list) else (src or "")
                rows.append({"Figure": n["name"], "Value": "n/a" if n["value"] is None else f"{n['value']:,.2f}", "Source": where})
            rows += [{"Figure": "quote", "Value": "", "Source": f"p.{e['page']} {e['section']}: “{e['quote']}”"} for e in f.get("evidence", [])]
            if rows:
                show_rows(rows)
            st.caption(f"Documents to request: {', '.join(f['docs_needed']) or 'none listed'} · status: {'answered' if mine else f['status']} · version {mine[-1]['version'] if mine else f['version']}")
            if ANALYST:
                for a in mine:
                    st.success(f"**Answer ({a['answered_by']}, {a['at'][:10]}, {a['version']}):** {a['answer']}" + (f"  \n_Source:_ {a['source_note']}" if a["source_note"] else ""))
                with st.form(f"ans_{f['id']}", clear_on_submit=True):
                    txt = st.text_area("Record an answer to this finding's question", key=f"t_{f['id']}", height=90)
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

# ------------------------------------------------------------------ Peers
elif page == "Peers":
    if not ANALYST:
        st.info("The peer comparison uses third-party market data and is not part of the public demo. It is available in analyst mode.")
    else:
        for name, title in (("peer_comparison.md", "Industry comparison"), ("dupont_peers.md", "DuPont, peers")):
            md = appdata.internal_markdown(ticker, fy, name)
            st.markdown(md if md else f"*{title}: not built for this company-year.*")
            st.divider()

# ------------------------------------------------------------------ Sources and data quality
elif page == "Sources and data quality":
    st.subheader("Source documents")
    srcs = rep.get("sources") or (appdata.sources_for(ticker, fy) if ANALYST else [])
    for s in srcs:
        st.markdown(f"- {s.get('description', 'Annual report')} — [source link]({s.get('url')}) · downloaded {s.get('downloaded')} · SHA-256 `{(s.get('sha256') or '')[:16]}…`")
    st.caption("Source PDFs are linked, never copied. Every number in the report carries its statement and page.")
    st.subheader("Data notes and disclaimers")
    order = {"important": 0, "note": 1, "standard": 2}
    for n in sorted(rep["data_notes"], key=lambda n: order.get(n["severity"], 3)):
        st.markdown(f"- **{n['category'].replace('_', ' ')}** ({n['severity']}): {n['text']}")
    ar = rep.get("auditor_report")
    if ar:
        st.subheader("Auditor's report as read by code")
        st.markdown(f"Pages {ar['pages'][0]}-{ar['pages'][1]} of the PDF · firm {ar['firm'] or 'not identified'} · opinion {ar['opinion']['type']}. Every quote is verified against the PDF text.")
        if ar["opinion"].get("quote"):
            st.markdown(f"> {ar['opinion']['quote']}  \n> — p.{ar['opinion']['page']}")
        for e in ar["emphasis_of_matter"] + ar["other_matter"]:
            st.markdown(f"> {e['quote']}  \n> — p.{e['page']}, {e['section']}")
        if not ar.get("signature_found"):
            st.warning("The report's signature block was not found in the PDF text, so its end may be incomplete.")
    u = rep.get("usd_view")
    if u and u.get("status") == "ok":
        st.subheader("Macro data used")
        st.markdown(f"{u['source']} · vintage {u['vintage_id']} · retrieved {u['retrieved_at'][:10]} · file SHA-256 `{u['raw_file_sha256'][:16]}…`" + (" · **older than 90 days**" if u["vintage_older_than_limit"] else ""))
    if ANALYST and rep.get("ratings"):
        st.subheader("Data-quality inputs (analyst)")
        st.json(rep["ratings"]["data_quality"].get("inputs", {}))

# ------------------------------------------------------------------ Ask the agent (analyst only)
elif page == "Ask the agent":
    from fsa.app import ask as askmod

    st.markdown(
        f"Ask about **{rep['company']} FY{fy}**. The agent answers only from this report, cites where, and says so when something is not in it. "
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
