"""Command-line entry point. Run with:  uv run fsa <command>"""
import argparse
import json
from datetime import date
from pathlib import Path

import yaml

from fsa.ingest.inbox import process_inbox
from fsa.ingest.ngnmarket import load_export
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


def main() -> None:
    p = argparse.ArgumentParser(prog="fsa")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("load-ngnmarket", help="Load a company's NGN Market exports from data/raw/ngnmarket/<TICKER>/")
    s.add_argument("ticker")
    s.set_defaults(func=cmd_load_ngnmarket)
    i = sub.add_parser("ingest-inbox", help="Rename, file and load every CSV in 'companies financial reports/'")
    i.add_argument("--dry-run", action="store_true", help="Show what would happen without moving or loading anything")
    i.set_defaults(func=cmd_ingest_inbox)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
