"""Process the inbox folder of raw NGN Market CSV exports.

For each CSV: work out the company (from the file name), whether it is annual or quarterly and the
timeline covered (from the column headers), rename it, file it under data/raw/ngnmarket/<TICKER>/,
write a source record next to it, and load it into the warehouse. Anything we cannot identify
with certainty is left in place and reported. We never guess.
"""
import json
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from fsa.ingest.ngnmarket import load_export, parse_period, parse_sections, sha256_of

NAME_RE = re.compile(r"^(?P<ticker>.+?)-financials-(?P<dl>\d{4}-\d{2}-\d{2})", re.IGNORECASE)
EXPECTED = {"Income Statement", "Balance Sheet", "Cash Flow", "Ratios", "Dividends"}


@dataclass
class Detected:
    path: Path
    ticker: str
    view: str          # annual | quarterly
    timeline: str      # e.g. 2012-2025 or 2013Q1-2025Q4
    downloaded: date
    sha256: str


def slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")


def detect(path: Path) -> Detected:
    m = NAME_RE.match(path.name)
    if not m:
        raise ValueError("file name does not look like '<TICKER>-financials-<date>.csv'")
    sections = parse_sections(path)
    unknown = set(sections) - EXPECTED
    if unknown or "Income Statement" not in sections:
        raise ValueError(f"unexpected sections: {sorted(unknown) or 'no Income Statement'}")
    labels = sections["Income Statement"][0][1:]
    periods = [parse_period(x) for x in labels]
    kinds = {p[0] for p in periods}
    if kinds == {"FY"}:
        view = "annual"
        years = [p[1] for p in periods]
        timeline = f"{min(years)}-{max(years)}"
    elif kinds == {"Q"}:
        view = "quarterly"
        ordered = sorted((p[1], p[2]) for p in periods)
        timeline = f"{ordered[0][0]}Q{ordered[0][1]}-{ordered[-1][0]}Q{ordered[-1][1]}"
    else:
        raise ValueError("mixes annual and quarterly columns")
    return Detected(path, m["ticker"].upper(), view, timeline, date.fromisoformat(m["dl"]), sha256_of(path))


def new_name(d: Detected, company_name: str | None) -> str:
    name = slug(company_name) if company_name else ""
    if name.lower() == d.ticker.lower():
        name = ""  # don't repeat the ticker
    parts = [d.ticker] + ([name] if name else []) + [d.view, d.timeline, f"dl{d.downloaded.isoformat()}"]
    return "_".join(parts) + ".csv"


def process_inbox(con, inbox: Path, raw_root: Path, known_names: dict[str, str], dry_run: bool = False) -> list[str]:
    report: list[str] = []
    known_hashes = {r[0] for r in con.execute("SELECT sha256 FROM source_files").fetchall()}
    for path in sorted(inbox.glob("*.csv")):
        try:
            d = detect(path)
        except Exception as e:  # leave the file alone and say why
            report.append(f"SKIPPED  {path.name}: {e}")
            continue
        if d.sha256 in known_hashes:
            report.append(f"DUPLICATE {path.name}: already loaded ({d.ticker} {d.view} {d.timeline})")
            if not dry_run:
                dup = inbox / "_duplicates"
                dup.mkdir(exist_ok=True)
                shutil.move(str(path), str(dup / path.name))
            continue
        name = new_name(d, known_names.get(d.ticker))
        report.append(f"LOAD     {path.name}  ->  {d.ticker}/{name}")
        if dry_run:
            continue
        dest_dir = raw_root / d.ticker
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / name
        if dest.exists():
            report.append(f"SKIPPED  {path.name}: {dest.name} already exists")
            continue
        existing = con.execute("SELECT 1 FROM companies WHERE ticker = ?", [d.ticker]).fetchone()
        if not existing:
            # Peers can be stored. Analysis and publication still require conflict_check = 'pass' (rule 3).
            con.execute(
                "INSERT INTO companies (ticker, name, role, conflict_check) VALUES (?,?,?,?)",
                [d.ticker, known_names.get(d.ticker, d.ticker), "peer", "unchecked"],
            )
        shutil.move(str(path), str(dest))
        counts = load_export(con, dest, d.ticker, d.view, d.downloaded)
        dest.with_suffix(".source.json").write_text(json.dumps({
            "provider": "ngnmarket", "ticker": d.ticker, "view": d.view, "timeline": d.timeline,
            "downloaded": d.downloaded.isoformat(), "original_name": path.name, "sha256": d.sha256,
            "loaded": datetime.now().isoformat(timespec="seconds"),
            "terms_note": "Private use only. Not for redistribution or publication (NGN Market ToS s.10-11).",
        }, indent=2))
        report.append(f"         loaded {counts}")
        known_hashes.add(d.sha256)
    return report
