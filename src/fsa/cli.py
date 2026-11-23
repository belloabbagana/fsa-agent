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
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
