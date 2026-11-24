"""Command-line entry point. Run with:  uv run fsa <command>"""
import argparse
import csv
import json
from datetime import date
from pathlib import Path

import yaml

from fsa.extract.pdf_statements import extract_statements
from fsa.ingest.inbox import process_inbox
from fsa.ingest.ngnmarket import load_export
from fsa.ingest.ngnmarket import sha256_of
from fsa.map.chart import coverage, load_chart, validate_chart
from fsa.map.model_proposer import WrongModelError, ask, build_prompt, model_check, parse_proposals, verify_model
from fsa.map.finalize import finalize, load_decisions, signed_off
from fsa.map.rules_mapper import propose as rule_propose
from fsa.analyse import comps, forensic as fz
from fsa.analyse import ratios as rat
from fsa.classify import draft as gdraft
from fsa.findings import records as frec
from fsa.findings import writer as fwr
from fsa.findings import triggers as trg
from fsa.report import disclosures as disc
from fsa.map.validate_mapping import check_identities, check_subtotals
from fsa.validate.statements import run_comparatives, run_identities, run_reconciliation
from fsa.warehouse.db import ROOT, connect


def sync_companies(con) -> None:
    cfg = yaml.safe_load((ROOT / "config" / "companies.yaml").read_text())
    for c in cfg["companies"]:
        con.execute(
            """INSERT INTO companies (ticker, name, role, conflict_check, fiscal_year_end) VALUES (?,?,?,?,?)
               ON CONFLICT (ticker) DO UPDATE SET name = excluded.name, role = excluded.role,
               conflict_check = excluded.conflict_check, fiscal_year_end = excluded.fiscal_year_end""",
            [c["ticker"], c["name"], c["role"], c["conflict_check"], c["fiscal_year_end"]],
        )


def cmd_ingest_inbox(args) -> None:
    con = connect()
    sync_companies(con)
    cfg = yaml.safe_load((ROOT / "config" / "companies.yaml").read_text())
    names = {c["ticker"]: c["name"].replace(" Plc", "") for c in cfg["companies"]}
    inbox = ROOT / "companies financial reports"
    for line in process_inbox(con, inbox, ROOT / "data" / "raw" / "ngnmarket", names, dry_run=args.dry_run):
        print(line)
    if args.dry_run:
        print("(dry run: nothing moved or loaded)")


def cmd_load_ngnmarket(args) -> None:
    folder = ROOT / "data" / "raw" / "ngnmarket" / args.ticker
    meta = json.loads((folder / "source.json").read_text())
    con = connect()
    sync_companies(con)
    status = con.execute("SELECT conflict_check FROM companies WHERE ticker = ?", [args.ticker]).fetchone()
    if not status or status[0] != "pass":
        raise SystemExit(f"{args.ticker}: conflict check is not 'pass' in config/companies.yaml. Not loading (rule 3).")
    for f in meta["files"]:
        counts = load_export(con, folder / f["file"], args.ticker, f["view"], date.fromisoformat(meta["downloaded"]))
        print(f"{f['file']}: {counts}")

def cmd_extract_pdf(args) -> None:
    ticker, fy = args.ticker, int(args.fy.upper().replace("FY", ""))
    folder = ROOT / "data" / "raw" / ticker / f"FY{fy}"
    pdfs = sorted(folder.glob("*.pdf"))
    if len(pdfs) != 1:
        raise SystemExit(f"Expected exactly one PDF in {folder}, found {len(pdfs)}.")
    pdf = pdfs[0]
    con = connect()
    sync_companies(con)
    status = con.execute("SELECT conflict_check FROM companies WHERE ticker = ?", [ticker]).fetchone()
    if not status or status[0] != "pass":
        raise SystemExit(f"{ticker}: conflict check is not 'pass'. Not extracting (rule 3).")
    sha = sha256_of(pdf)
    source_id = sha[:16]
    meta = json.loads((folder / "source.json").read_text())
    con.execute("DELETE FROM pdf_statement_lines WHERE source_id = ?", [source_id])
    con.execute("DELETE FROM source_files WHERE source_id = ?", [source_id])
    con.execute("INSERT INTO source_files (source_id, sha256, file_name, provider, ticker, view, downloaded) VALUES (?,?,?,?,?,?,?)",
                [source_id, sha, pdf.name, "annual_report", ticker, "pdf", date.fromisoformat(meta["downloaded"])])
    statements = extract_statements(pdf, fy)
    out_rows = []
    for name, (page, columns, unit, lines) in statements.items():
        for ln in lines:
            for (value, flag), (ent, pend) in zip(ln.values, columns):
                u = "NGN_per_share" if "(naira)" in ln.label.lower() else ("KOBO_per_share" if "(kobo)" in ln.label.lower() else unit)
                con.execute("INSERT INTO pdf_statement_lines VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            [ticker, fy, name, ln.seq, ln.section, ln.label, ln.note_ref, ent, pend, value, u, flag, page, source_id])
                out_rows.append([name, page, ln.seq, ln.section.replace("\x00", "·"), ln.label.replace("\x00", "·"), ln.note_ref, ent, pend, value, u, flag])
    interim = ROOT / "data" / "interim" / ticker / f"FY{fy}"
    interim.mkdir(parents=True, exist_ok=True)
    with open(interim / "statements_extracted.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["statement", "page", "line", "section", "label", "note_ref", "entity", "period_end", "value", "unit", "flag"])
        w.writerows(out_rows)
    print(f"Extracted {len(out_rows)} values from {pdf.name}")
    for name, (page, columns, unit, lines) in statements.items():
        bad = sum(1 for ln in lines if ln.problems)
        print(f"  {name:22s} page {page:>3}  {len(lines):>3} lines  {len(columns)} columns  unit {unit}  ({bad} flagged)")
    ids = run_identities(con, ticker, fy)
    if ids is None:
        print(f"\nNo integrity rules for {ticker} FY{fy} yet (rules/integrity/statement_identities.yaml): values stored, checks skipped.")
        return
    passed = sum(1 for r in ids if r[6] == "pass")
    print(f"\nArithmetic checks: {passed} of {len(ids)} pass")
    for statement, entity, pend, name, total, actual, st in ids:
        if st != "pass":
            print(f"  FAIL {statement} / {entity} / {pend} / {name}: printed {total}, parts add to {actual}  [{st}]")
    comp = run_comparatives(con, ticker, fy)
    if comp is not None:
        from collections import Counter as C
        counts = C(r[5] for r in comp)
        print(f"\nComparatives vs the FY{fy-1} report: {dict(counts)}")
        for st, ent, label, orig, now, status in comp:
            if status == "fail":
                print(f"  CHANGED {st} / {ent} / {label.replace(chr(0), '·')}: FY{fy-1} report said {orig}, FY{fy} report shows {now}")
    rec = run_reconciliation(con, ticker, fy)
    from collections import Counter
    print("\nNGN Market reconciliation (group, FY%d): %s" % (fy, dict(Counter(r[5].split(" ")[0] for r in rec))))
    for metric, label, pv, prv, diff, st in rec:
        if st.startswith("differs") or st.startswith("not_comparable ("):
            print(f"  {st:16s} {metric}: PDF {pv}, provider {prv}, difference {diff}")

def cmd_chart_check(args) -> None:
    chart = load_chart()
    problems = validate_chart(chart)
    accounts = chart["accounts"]
    print(f"Chart v{chart['version']}: {len(accounts)} accounts "
          f"({sum(1 for a in accounts if a['kind'] == 'line')} lines, {sum(1 for a in accounts if a['kind'] == 'subtotal')} subtotals)")
    print("Well formed." if not problems else "Problems:\n  " + "\n  ".join(problems))
    con = connect()
    seen, unknown = coverage(con, chart)
    total = len(seen)
    print(f"\nPrinted labels in the extracted reports: {total} distinct (after ignoring spacing and broken ligatures)")
    print(f"  recognised by the chart: {total - len(unknown)}   not yet recognised: {len(unknown)}")
    for st, label, where in sorted(unknown):
        print(f"    [{st}] {label[:70]}   ({where})")

def cmd_model_check(args) -> None:
    """Prove which model really answers when we ask for one."""
    try:
        r = model_check(args.model)
    except WrongModelError as e:
        raise SystemExit(f"MODEL CHECK FAILED: {e}")
    except Exception as e:
        raise SystemExit(f"The model call did not work: {e}\nIf it says 'Not logged in', sign the bundled tool in first (see the instructions in chat).")
    print(f"Asked for:        {r['requested']}")
    print(f"Actually used:    {', '.join(r['answered_by'])}   (read from the usage report, not from the model's own claim)")
    print(f"Reply:            {r['reply']!r}")
    print(f"Cost reported:    {r['cost_usd']}")


def _load_group_lines(con, ticker, fy):
    rows = con.execute("""SELECT statement, seq, section, label, value FROM pdf_statement_lines
                          WHERE ticker=? AND fiscal_year=? AND entity='group' AND period_end=?
                          ORDER BY statement, seq""", [ticker, fy, date(fy, 12, 31)]).fetchall()
    return [dict(statement=a, seq=b, section=c or "", label=d or "", value=v) for a, b, c, d, v in rows]


def _store(con, ticker, fy, proposals, proposer, model_used=None):
    con.execute("DELETE FROM line_mappings WHERE ticker=? AND fiscal_year=? AND proposer=?", [ticker, fy, proposer])
    for p in proposals:
        con.execute("INSERT INTO line_mappings (ticker, fiscal_year, statement, seq, section, label, proposer, code, confidence, reason, model_used) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [ticker, fy, p["statement"], p["seq"], p["section"], p["label"], proposer, p["code"], p["confidence"], p["reason"], model_used])


def _arith(chart, lines, proposals):
    mapping = {(p["statement"], p["seq"]): p["code"] for p in proposals if p["code"]}
    values = {(l["statement"], l["seq"]): l["value"] for l in lines}
    return check_subtotals(chart, mapping, values)


def cmd_map_lines(args) -> None:
    ticker, fy = args.ticker, int(args.fy.upper().replace("FY", ""))
    chart = load_chart()
    valid = {a["code"] for a in chart["accounts"]}
    con = connect()
    lines = _load_group_lines(con, ticker, fy)
    if not lines:
        raise SystemExit(f"No extracted lines for {ticker} FY{fy}. Run extract-pdf first.")
    base = rule_propose(chart, lines)
    _store(con, ticker, fy, base, "rule")
    n_code = sum(1 for p in base if p["code"])
    print(f"Rule baseline: {n_code} of {len(base)} lines mapped by code alone ({len(base) - n_code} left without a code)")
    arith = _arith(chart, lines, base)
    bad = [r for r in arith if r["status"] != "pass"]
    print(f"  arithmetic: {len(arith) - len(bad)} of {len(arith)} printed subtotals equal the sum of their mapped parts")
    for r in bad:
        print(f"    DIFFERS {r['code']}: printed {r['printed']:,.0f}, mapped parts add to {r['computed']:,.0f} (difference {r['difference']:,.0f})")
    idn = check_identities(chart, {(p["statement"], p["seq"]): p["code"] for p in base if p["code"]},
                           {(l["statement"], l["seq"]): l["value"] for l in lines})
    if idn:
        print(f"  identities: {sum(1 for r in idn if r['status'] == 'pass')} of {len(idn)} hold ({', '.join(r['name'] + ' vs ' + r['against'] for r in idn)})")
        for r in idn:
            if r["status"] != "pass":
                print(f"    DIFFERS {r['name']}: printed {r['total']:,.0f}, parts add to {r['parts']:,.0f}")

    model_props, model_name, notes = None, None, []
    if args.model and args.model != "none":
        by_stmt = {}
        for p in base:
            by_stmt.setdefault(p["statement"], []).append(p)
        model_props, used_all = [], set()
        for stmt, plist in by_stmt.items():
            items = [dict(id=f"{p['statement']}:{p['seq']}", section=p["section"], label=p["label"]) for p in plist]
            ans = ask(build_prompt(chart, stmt, items), args.model)
            if ans.is_error:
                raise SystemExit(f"The model call failed: {ans.error_text}\nIf it says 'Not logged in', sign in as described in chat.")
            used_all |= verify_model(ans, args.model)            # stops the run if a different model answered
            got, n = parse_proposals(ans.text, valid, {i["id"] for i in items})
            notes += n
            ids = {x["id"]: x for x in got}
            for p in plist:
                x = ids.get(f"{p['statement']}:{p['seq']}")
                model_props.append({**{k: p[k] for k in ("statement", "seq", "section", "label")}, "proposer": args.model,
                                    "code": x["code"] if x else None, "confidence": (x or {}).get("confidence"), "reason": (x or {}).get("reason", "no answer")})
        model_name = ", ".join(sorted(used_all))
        _store(con, ticker, fy, model_props, args.model, model_name)
        print(f"\n{args.model}: answered by {model_name} (verified from the usage report)")

    # compare and write the review sheet
    out_dir = ROOT / "data" / "interim" / ticker / f"FY{fy}"
    out_dir.mkdir(parents=True, exist_ok=True)
    base_by = {(p["statement"], p["seq"]): p for p in base}
    rows_disagree, rows_open = [], []
    agree = 0
    for p in base:
        k = (p["statement"], p["seq"])
        m = next((x for x in (model_props or []) if (x["statement"], x["seq"]) == k), None)
        if m is None:
            if p["code"] is None:
                rows_open.append(p)
            continue
        if p["code"] == m["code"]:
            agree += 1
        elif p["code"] is None and m["code"] is None:
            rows_open.append(p)
        else:
            rows_disagree.append((p, m))
    lines_md = [f"# Mapping review: {ticker} FY{fy}", "",
                f"- Lines (Group, current year): {len(lines)}",
                f"- Rule baseline: {n_code} mapped by code alone, arithmetic check {len(arith) - len(bad)}/{len(arith)} printed subtotals reproduced"]
    if model_props is not None:
        lines_md += [f"- {args.model} (answered by {model_name}): agrees with the baseline on {agree}, disagrees on {len(rows_disagree)}", ""]
        arith_m = _arith(chart, lines, [m for m in model_props if m["code"]])
        bad_m = [r for r in arith_m if r["status"] != "pass"]
        lines_md += [f"- Arithmetic check on the model's own mapping: {len(arith_m) - len(bad_m)}/{len(arith_m)} printed subtotals reproduced"]
        if notes:
            lines_md += ["- Notes: " + "; ".join(notes)]
    lines_md += ["", "## For you to decide", "", "| Statement | Line | Section | Label | Rule says | Model says | Why (model) |", "|---|---|---|---|---|---|---|"]
    for p, m in rows_disagree:
        lines_md.append(f"| {p['statement']} | {p['seq']} | {p['section'][:24]} | {p['label'][:46]} | {p['code'] or '-'} | {m['code'] or '-'} | {(m['reason'] or '')[:60]} |")
    for p in rows_open:
        lines_md.append(f"| {p['statement']} | {p['seq']} | {p['section'][:24]} | {p['label'][:46] or '(blank)'} | - | - | {p['reason'][:60]} |")
    if bad:
        lines_md += ["", "## Printed subtotals that the rule mapping does not reproduce", ""]
        lines_md += [f"- {r['name']}: printed {r['printed']:,.0f}, parts add to {r['computed']:,.0f}" for r in bad]
    (out_dir / "mapping_review.md").write_text("\n".join(lines_md) + "\n")
    if model_props is not None:
        print(f"  agrees with the rule baseline on {agree} lines; disagrees on {len(rows_disagree)}; no code from either on {len(rows_open)}")
        print(f"  arithmetic on the model's mapping: {len(arith_m) - len(bad_m)} of {len(arith_m)} printed subtotals reproduced")
        for p, m in rows_disagree[:15]:
            print(f"    DISAGREE [{p['statement']}] {p['label'][:40]!r}: rule={p['code']} {args.model}={m['code']}")
    print(f"\nReview sheet: {out_dir / 'mapping_review.md'}")

def cmd_finalize_mapping(args) -> None:
    ticker, fy = args.ticker, int(args.fy.upper().replace("FY", ""))
    chart, con = load_chart(), connect()
    lines = _load_group_lines(con, ticker, fy)
    def codes(proposer):
        rows = con.execute("SELECT statement, seq, code FROM line_mappings WHERE ticker=? AND fiscal_year=? AND proposer=?", [ticker, fy, proposer]).fetchall()
        return {(a, b): c for a, b, c in rows} if rows else None
    rule, model = codes("rule"), codes("haiku")
    if rule is None:
        raise SystemExit("Run map-lines first (no rule proposals stored).")
    final = finalize(chart, lines, rule, model, load_decisions())
    con.execute("DELETE FROM mapping_final WHERE ticker=? AND fiscal_year=?", [ticker, fy])
    for r in final:
        con.execute("INSERT INTO mapping_final VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [ticker, fy, r["statement"], r["seq"], r["label"], r["code"], r["basis"], r["status"], r["rule_code"], r["model_code"], r["note"]])
    from collections import Counter
    basis = Counter(r["basis"] for r in final)
    open_rows = [r for r in final if r["status"] == "open"]
    print(f"{ticker} FY{fy}: {len(final)} lines. " + ", ".join(f"{k} {v}" for k, v in sorted(basis.items())))
    arith = _arith(chart, lines, [{"statement": r["statement"], "seq": r["seq"], "code": r["code"]} for r in final if r["code"]])
    bad = [r for r in arith if r["status"] != "pass"]
    print(f"  arithmetic on the settled mapping: {len(arith) - len(bad)} of {len(arith)} printed subtotals reproduced")
    for r in bad:
        print(f"    differs: {r['name']} (printed {r['printed']:,.0f}, standardised {r['computed']:,.0f})")
    print(f"  open lines: {len(open_rows)}")
    for r in open_rows:
        print(f"    OPEN [{r['statement']}] {r['label'][:44]!r}: rule={r['rule_code']} model={r['model_code']} {r['note']}")
    so = signed_off(ticker, fy)
    print("  sign-off: " + (f"approved by {so.get('approved_by')} on {so.get('date')}" if so and not open_rows else
                           ("signed in config but lines are still open: NOT approved" if so else "not yet signed off by the analyst")))


def cmd_disclosures(args) -> None:
    ticker, fy = args.ticker, int(args.fy.upper().replace("FY", ""))
    chart, con = load_chart(), connect()
    items = disc.generate(con, chart, ticker, fy)
    disc.save(con, ticker, fy, items)
    out_dir = ROOT / "data" / "interim" / ticker / f"FY{fy}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "disclosures.md").write_text(disc.to_markdown(ticker, fy, items))
    (out_dir / "disclosures.json").write_text(json.dumps(items, indent=2, ensure_ascii=False))
    from collections import Counter
    print(f"{ticker} FY{fy}: {len(items)} notes " + str(dict(Counter(i['severity'] for i in items))))
    if args.show:
        print(disc.to_markdown(ticker, fy, items))


def cmd_decisions(args) -> None:
    con = connect()
    for d in load_decisions():
        n = con.execute("""SELECT count(*) FROM mapping_final f WHERE f.label IS NOT NULL AND lower(replace(f.label,' ','')) LIKE '%' || lower(replace(?, ' ', '')) || '%'""", [d["label"][:20]]).fetchone()[0]
        print(f"[{d['status']:8s}] {d['label']!r} -> {d['code']}   (seen on ~{n} line(s))\n           {d['reason']}")

def cmd_ratios(args) -> None:
    ticker, fy = args.ticker, int(args.fy.upper().replace("FY", ""))
    chart, con, rules = load_chart(), connect(), rat.load_rules()
    res = rat.compute(con, chart, rules, ticker, fy)
    rat.save(con, ticker, fy, res, rules["version"])
    out_dir = ROOT / "data" / "interim" / ticker / f"FY{fy}"
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = [f"# Ratios: {ticker} FY{fy} (rules v{rules['version']}, chart v{chart['version']}, N millions, nominal)", ""]
    cat = None
    for r in res:
        if r["category"] != cat:
            cat = r["category"]
            lines += ["", f"## {cat.replace('_', ' ')}", ""]
        lines.append(f"- {r['ratio_id'].replace('_', ' ')}: {rat.fmt(r)}")
    (out_dir / "ratios.md").write_text("\n".join(lines) + "\n")
    n_nm = sum(1 for r in res if r["status"] != "ok")
    print(f"{ticker} FY{fy}: {len(res)} ratios, {len(res) - n_nm} computed, {n_nm} not meaningful")
    if args.show:
        print("\n".join(lines))

def cmd_draft_gics(args) -> None:
    cfg = yaml.safe_load((ROOT / "config" / "companies.yaml").read_text())["companies"]
    labels = gdraft.load_ngx_labels()
    companies = [{"ticker": c["ticker"], "name": c["name"], "sector": labels[c["ticker"]]["sector"], "sub_sector": labels[c["ticker"]]["sub_sector"]}
                 for c in cfg if c["ticker"] in labels and not c.get("optional")]
    model = yaml.safe_load((ROOT / "config" / "models.yaml").read_text()).get("draft_gics", "haiku")
    items, used, problems = gdraft.draft(companies, model)
    gdraft.write_yaml(items, companies, used, problems)
    print(f"Drafted {len(items)} of {len(companies)} companies with {', '.join(sorted(used))} (verified from the usage report).")
    for p in problems:
        print("  REJECTED:", p)
    for ticker, v in yaml.safe_load(gdraft.OUT.read_text())["gics"].items():
        print(f"  {ticker:11s} {v['sector']} > {v['industry_group']} > {v['industry']} > {v['sub_industry']}  [{v['confidence']}]")


def cmd_gics_sync(args) -> None:
    con = connect()
    sync_companies(con)
    data = yaml.safe_load((ROOT / "config" / "gics_map.yaml").read_text())["gics"]
    n = 0
    for t, v in data.items():
        if v.get("status") == "approved":
            con.execute("UPDATE companies SET gics_sector=?, gics_industry_group=?, gics_industry=?, gics_sub_industry=? WHERE ticker=?",
                        [v["sector"], v["industry_group"], v["industry"], v["sub_industry"], t])
            n += 1
    print(f"{n} approved classification(s) loaded; {len(data) - n} still proposed (not used).")
    for r in con.execute("SELECT ticker, gics_industry, gics_sub_industry FROM companies ORDER BY gics_industry NULLS LAST, ticker").fetchall():
        print("  ", r)

def cmd_comps(args) -> None:
    chart, con, rules = load_chart(), connect(), rat.load_rules()
    a = comps.compute_all(con, chart, rules)
    b = comps.stats_and_positions(con)
    print(f"Comparison ratios: {a['rows']} values for {a['companies']} companies; statistics for {b['industries']} industries.")
    rows = con.execute("""SELECT ticker, fiscal_year, position, count(*) FROM peer_position GROUP BY ALL ORDER BY 1,2,3""").fetchall()
    for t, fy, pos, n in rows:
        print(f"  {t} FY{fy}: {pos} x{n}")


def cmd_comps_show(args) -> None:
    ticker, fy = args.ticker, int(args.fy.upper().replace("FY", ""))
    con = connect()
    ind = con.execute("SELECT gics_industry FROM companies WHERE ticker=?", [ticker]).fetchone()[0]
    mem = [r[0] for r in con.execute("SELECT ticker FROM companies WHERE gics_industry=? ORDER BY ticker", [ind]).fetchall()]
    unchecked = [r[0] for r in con.execute("SELECT ticker FROM companies WHERE gics_industry=? AND ticker<>? AND conflict_check<>'pass'", [ind, ticker]).fetchall()]
    lines = [f"# Industry comparison: {ticker} FY{fy} vs {ind}", ""]
    if unchecked:
        lines += [f"> **INTERNAL DRAFT: not for publication.** {len(unchecked)} peer(s) have no conflict check recorded ({', '.join(unchecked)}). Peer names and values must stay internal until each is recorded as pass.", ""]
    lines += [f"Group: {len(mem)} companies ({', '.join(mem)}). All figures from NGN Market data for every company (same definitions); the audited-basis figure for {ticker} is shown for reference. "
              "'Small peer set' means fewer than 5 peers, so the median is indicative. Position says where the number sits, not whether that is good or bad.", ""]
    audited = {r: (v, u) for r, v, u in con.execute("SELECT ratio_id, value, unit FROM ratios WHERE ticker=? AND fiscal_year=? AND status='ok'", [ticker, fy]).fetchall()}
    audited.update({r: (v, u) for r, v, u in con.execute("SELECT item_id, value, unit FROM forensic_scores WHERE ticker=? AND fiscal_year=? AND status='ok'", [ticker, fy]).fetchall()})
    cats = dict(con.execute("SELECT DISTINCT ratio_id, category FROM ratios").fetchall())
    cats.update({r: "forensic" for (r,) in con.execute("SELECT DISTINCT item_id FROM forensic_scores").fetchall()})
    cur_cat = None
    rows = con.execute("""SELECT p.ratio_id, p.value, p.peer_n, p.peer_median, p.peer_min, p.peer_max, p.position, p.small_set, r.unit
                          FROM peer_position p JOIN (SELECT DISTINCT ratio_id, unit FROM comparison_ratios) r USING (ratio_id)
                          WHERE p.ticker=? AND p.fiscal_year=?""", [ticker, fy]).fetchall()
    rows.sort(key=lambda x: (cats.get(x[0], ""), x[0]))
    lines += ["| Ratio | " + ticker + " (comparison basis) | Audited basis | Peers (n) | Peer median | Peer range | Position |", "|---|---|---|---|---|---|---|"]
    def f(v, u):
        return "N/M" if v is None else {"pct": f"{v:,.1f}%", "x": f"{v:,.2f}x", "days": f"{v:,.0f} d", "pp": f"{v:+,.1f} pts", "ratio": f"{v:,.3f}", "score": f"{v:,.2f}"}.get(u, f"{v:,.2f}")
    cur_cat = None
    for rid, v, pn, med, mn, mx, pos, small, unit in rows:
        if cats.get(rid) != cur_cat:
            cur_cat = cats.get(rid)
            lines.append(f"| **{(cur_cat or '').replace('_', ' ')}** | | | | | | |")
        a = audited.get(rid)
        if v is None:
            lines.append(f"| {rid.replace('_', ' ')} | N/M | {f(a[0], unit) if a else 'N/M'} | - | - | - | not meaningful |")
        else:
            lines.append(f"| {rid.replace('_', ' ')} | {f(v, unit)} | {f(a[0], unit) if a else 'N/M'} | {pn if pn is not None else '-'}{' (small set)' if small and pn else ''} | "
                         f"{f(med, unit)} | {f(mn, unit)} to {f(mx, unit)} | {pos} |")
    why = con.execute("SELECT ratio_id, reason FROM comparison_ratios WHERE ticker=? AND fiscal_year=? AND status='N/M' ORDER BY 1", [ticker, fy]).fetchall()
    if why:
        lines += ["", "### Why some ratios are not meaningful on the comparison basis", ""] + [f"- {r.replace('_', ' ')}: {reason}" for r, reason in why]
    out = ROOT / "data" / "interim" / ticker / f"FY{fy}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "peer_comparison.md").write_text("\n".join(lines) + "\n")
    n_ok = sum(1 for r in rows if r[1] is not None)
    print(f"{ticker} FY{fy}: {n_ok} of {len(rows)} ratios comparable; wrote {out / 'peer_comparison.md'}")
    if args.show:
        print("\n".join(lines))

def cmd_forensic(args) -> None:
    ticker, fy = args.ticker, int(args.fy.upper().replace("FY", ""))
    chart, con, rules, frules = load_chart(), connect(), rat.load_rules(), fz.load_rules()
    res = fz.compute(con, chart, rules, frules, ticker, fy)
    fz.save(con, ticker, fy, res, frules["version"])
    why = {i["id"]: i["why"] for i in frules["ratios"]}
    lines = [f"# Forensic indicators: {ticker} FY{fy} (rules v{frules['version']}, year-end balances for the two published models)", "",
             "> **Read with care.** These are statistical indicators calibrated on other markets. They are items to examine, never findings, and never read alone: "
             "devaluation and high inflation move several inputs (sales growth, receivables, asset values) for reasons unrelated to manipulation or distress. "
             "Compare each with the company's own history and with peers.", ""]
    for r in res:
        lines.append(f"- **{r['ratio_id'].replace('_', ' ')}**: {fz.fmt(r)}  \n  *{why[r['ratio_id']]}*")
    for item in frules["ratios"]:
        if item.get("coefficients"):
            rows = fz.contributions(item, res)
            if rows:
                lines += ["", f"### What drives the {item['id'].replace('_', ' ')} (largest first; a neutral company's indices are 1.0)", ""]
                lines += [f"- {k}: {v:,.3f} → contributes {x:+,.2f}" for k, v, x in rows[:4]]
    out = ROOT / "data" / "interim" / ticker / f"FY{fy}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "forensic.md").write_text("\n".join(lines) + "\n")
    byid = {r["ratio_id"]: r for r in res}
    print(f"{ticker} FY{fy}: Beneish M = {fz.fmt(byid['beneish_m'])}")
    print(f"           Altman Z'' = {fz.fmt(byid['altman_z'])}")
    flagged = [r["ratio_id"] for r in res if r.get("flagged")]
    print(f"           flags tripped: {', '.join(flagged) if flagged else 'none'}")
    if args.show:
        print("\n".join(lines))


def cmd_triggers(args) -> None:
    chart, con, ratio_rules = load_chart(), connect(), rat.load_rules()
    rules = trg.load_rules()
    pairs = [(t, y) for t, ys in {"DANGCEM": (2023, 2024, 2025), "PRESCO": (2023, 2024, 2025)}.items() for y in ys] if args.ticker == "all" \
        else [(args.ticker, int(args.fy.upper().replace("FY", "")))]
    for ticker, fy in pairs:
        res = trg.evaluate_rules(con, chart, ratio_rules, rules, ticker, fy)
        trg.save(con, ticker, fy, res, "0.1-draft")
        fired = [r for r in res if r["state"] == "fired"]
        recs = frec.build_records(con, chart, ratio_rules, rules, res, ticker, fy)
        out = ROOT / "data" / "interim" / ticker / f"FY{fy}"
        out.mkdir(parents=True, exist_ok=True)
        (out / "finding_candidates.json").write_text(json.dumps(recs, indent=2, default=str))
        (out / "finding_candidates.md").write_text(frec.to_markdown(recs, ticker, fy))
        order = {"high": 0, "medium": 1, "low": 2, "unrated": 3}
        print(f"\n== {ticker} FY{fy}: {len(fired)} fired, {sum(r['state']=='not fired' for r in res)} not fired, {sum(r['state']=='not tested' for r in res)} not tested (of {len(res)} rules)")
        for r in sorted(fired, key=lambda r: order.get(r["materiality"], 9)):
            m = f" ({r['measure']:.1f})" if r["measure"] is not None else ""
            note = f"  [{r['reason']}]" if r["reason"] else ""
            print(f"   {r['materiality'].upper():8} {r['rule_id']}  {r['title']}{m}{note}")
        if args.untested:
            for r in res:
                if r["state"] == "not tested":
                    print(f"   not tested {r['rule_id']}: {r['reason']}")


def cmd_write_findings(args) -> None:
    ticker, fy = args.ticker, int(args.fy.upper().replace("FY", ""))
    model = args.model or yaml.safe_load((ROOT / "config" / "models.yaml").read_text())["write_analysis"]
    out = ROOT / "data" / "interim" / ticker / f"FY{fy}"
    cand_path = out / "finding_candidates.json"
    if not cand_path.exists():
        raise SystemExit(f"No candidates for {ticker} FY{fy}. Run: ./fsa triggers {ticker} FY{fy}")
    cands = json.loads(cand_path.read_text())
    levels = {"high": 0, "medium": 1, "low": 2, "unrated": 3}
    cands = [c for c in cands if levels.get(c["materiality"], 9) <= levels[args.min_materiality]]
    done_path = out / "findings.json"
    done = {f["id"]: f for f in json.loads(done_path.read_text())} if done_path.exists() and not args.redo else {}
    todo = [c for c in cands if c["id"] not in done or done[c["id"]]["status"] != "written"]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{ticker} FY{fy}: {len(cands)} candidates at {args.min_materiality} or above; {len(todo)} to write with {model}; {len(done)} already kept")
    for c in todo:
        done[c["id"]] = fwr.write_one(c, model)                  # stops the run if a different model answered
        f = done[c["id"]]
        print(f"   {c['id']}: {f['status']} (tries {f['attempts']}, model {', '.join(f['written_by'])}" + (f", cost ${f['cost_usd']:.3f}" if f["cost_usd"] else "") + ")")
        ordered = [done[c2["id"]] for c2 in json.loads(cand_path.read_text()) if c2["id"] in done]
        done_path.write_text(json.dumps(ordered, indent=2, default=str))     # saved after each, so a paused run resumes
    ordered = [done[c2["id"]] for c2 in json.loads(cand_path.read_text()) if c2["id"] in done]
    (out / "findings.md").write_text(fwr.to_markdown(ordered, ticker, fy))
    print(f"wrote {out / 'findings.md'}")


def main() -> None:
    p = argparse.ArgumentParser(prog="fsa")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("load-ngnmarket", help="Load a company's NGN Market exports from data/raw/ngnmarket/<TICKER>/")
    s.add_argument("ticker")
    s.set_defaults(func=cmd_load_ngnmarket)
    i = sub.add_parser("ingest-inbox", help="Rename, file and load every CSV in 'companies financial reports/'")
    i.add_argument("--dry-run", action="store_true", help="Show what would happen without moving or loading anything")
    i.set_defaults(func=cmd_ingest_inbox)
    x = sub.add_parser("extract-pdf", help="Extract the statements from a downloaded annual report and check them")
    x.add_argument("ticker")
    x.add_argument("fy", help="e.g. FY2025")
    x.set_defaults(func=cmd_extract_pdf)
    k = sub.add_parser("chart-check", help="Validate the chart of accounts and show which printed labels it recognises")
    k.set_defaults(func=cmd_chart_check)
    mc = sub.add_parser("model-check", help="Prove which model really answers (default: haiku)")
    mc.add_argument("--model", default="haiku")
    mc.set_defaults(func=cmd_model_check)
    ml = sub.add_parser("map-lines", help="Map a company-year's printed lines to the chart of accounts")
    ml.add_argument("ticker")
    ml.add_argument("fy", help="e.g. FY2025")
    ml.add_argument("--model", default="none", help="haiku to also ask the model (needs the one-time sign-in); default: rules only")
    ml.set_defaults(func=cmd_map_lines)
    fm = sub.add_parser("finalize-mapping", help="Settle each line's code from rules, model and the analyst's decisions")
    fm.add_argument("ticker"); fm.add_argument("fy"); fm.set_defaults(func=cmd_finalize_mapping)
    dc = sub.add_parser("disclosures", help="Generate the report's data notes and disclaimers for a company-year")
    dc.add_argument("ticker"); dc.add_argument("fy"); dc.add_argument("--show", action="store_true"); dc.set_defaults(func=cmd_disclosures)
    dd = sub.add_parser("decisions", help="List the analyst decisions on mapping and their status")
    dd.set_defaults(func=cmd_decisions)
    rt = sub.add_parser("ratios", help="Compute the ratios in rules/ratios.yaml for a company-year")
    rt.add_argument("ticker"); rt.add_argument("fy"); rt.add_argument("--show", action="store_true"); rt.set_defaults(func=cmd_ratios)
    dg = sub.add_parser("draft-gics", help="Draft GICS classifications with a model (proposed only; the analyst approves)")
    dg.set_defaults(func=cmd_draft_gics)
    gs = sub.add_parser("gics-sync", help="Load the APPROVED classifications into the database")
    gs.set_defaults(func=cmd_gics_sync)
    cp = sub.add_parser("comps", help="Compute industry comparison ratios, statistics and peer positions")
    cp.set_defaults(func=cmd_comps)
    cs = sub.add_parser("comps-show", help="Write the peer comparison for a core company-year")
    cs.add_argument("ticker"); cs.add_argument("fy"); cs.add_argument("--show", action="store_true"); cs.set_defaults(func=cmd_comps_show)
    fo = sub.add_parser("forensic", help="Beneish M-score, Altman Z'' and red-flag tests for a company-year")
    fo.add_argument("ticker"); fo.add_argument("fy"); fo.add_argument("--show", action="store_true"); fo.set_defaults(func=cmd_forensic)
    tr = sub.add_parser("triggers", help="Dry-run the findings trigger rules (use 'all' for the six core company-years)")
    tr.add_argument("ticker"); tr.add_argument("fy", nargs="?", default=""); tr.add_argument("--untested", action="store_true"); tr.set_defaults(func=cmd_triggers)
    wf = sub.add_parser("write-findings", help="Write the prose for a company-year's finding candidates (model drafts, code checks)")
    wf.add_argument("ticker"); wf.add_argument("fy"); wf.add_argument("--model"); wf.add_argument("--limit", type=int, default=0)
    wf.add_argument("--min-materiality", default="medium", choices=["high", "medium", "low"]); wf.add_argument("--redo", action="store_true"); wf.set_defaults(func=cmd_write_findings)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
