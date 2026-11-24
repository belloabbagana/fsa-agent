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
from fsa.map.rules_mapper import propose as rule_propose
from fsa.map.validate_mapping import check_subtotals
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
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
