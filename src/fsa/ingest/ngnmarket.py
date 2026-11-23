"""Load an NGN Market CSV export (annual or quarterly) into the warehouse.

The export has several sections ("## Income Statement", "## Balance Sheet", "## Cash Flow",
"## Ratios", "## Dividends"). Values are in full naira. Blank cells become NULL, flagged 'missing';
they are never turned into zero. Nothing is corrected or re-signed here: we store what was exported.
"""
import csv
import hashlib
import re
from datetime import date
from pathlib import Path

STATEMENTS = {"Income Statement", "Balance Sheet", "Cash Flow"}
PERIOD = re.compile(r"^(FY (\d{4})|Q([1-4]) (\d{4}))$")
QUARTER_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_sections(path: Path) -> dict[str, list[list[str]]]:
    sections: dict[str, list[str]] = {}
    current = None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
        elif current and line.strip():
            sections[current].append(line)
    return {k: list(csv.reader(v)) for k, v in sections.items()}


def parse_period(label: str, fiscal_year_end: str = "12-31"):
    """'FY 2024' -> ('FY', 2024, None, date) ; 'Q3 2024' -> ('Q', 2024, 3, date).
    Assumes a calendar year-end (config/companies.yaml); revisit for other year-ends."""
    m = PERIOD.match(label.strip())
    if not m:
        raise ValueError(f"Unrecognised period label: {label!r}")
    if m.group(2):
        year = int(m.group(2))
        return "FY", year, None, date(year, 12, 31)
    q, year = int(m.group(3)), int(m.group(4))
    month, day = QUARTER_END[q]
    return "Q", year, q, date(year, month, day)


def to_number(cell: str):
    cell = cell.strip()
    if cell == "":
        return None
    return float(cell)


def statement_unit(metric: str) -> str:
    return "NGN_per_share" if metric.startswith("EPS") else "NGN"


def ratio_unit(metric: str) -> str:
    if "(%)" in metric:
        return "pct"
    if "Per Share" in metric:
        return "NGN_per_share"
    if metric.startswith("Days"):
        return "days"
    return "x"


def load_export(con, path: Path, ticker: str, view: str, downloaded: date, provider: str = "ngnmarket") -> dict:
    sha = sha256_of(path)
    source_id = sha[:16]
    sections = parse_sections(path)

    # Re-loading the same file replaces its rows instead of duplicating them.
    for table in ("statement_lines", "provider_ratios", "dividends"):
        con.execute(f"DELETE FROM {table} WHERE source_id = ?", [source_id])
    con.execute("DELETE FROM source_files WHERE source_id = ?", [source_id])
    con.execute(
        "INSERT INTO source_files (source_id, sha256, file_name, provider, ticker, view, downloaded) VALUES (?,?,?,?,?,?,?)",
        [source_id, sha, path.name, provider, ticker, view, downloaded],
    )

    # A newer download of the same company + view supersedes older ones (older rows are kept).
    con.execute(
        "UPDATE source_files SET is_current = (source_id = ?) WHERE ticker = ? AND view = ?",
        [source_id, ticker, view],
    )

    counts = {"statement_lines": 0, "provider_ratios": 0, "dividends": 0}
    for name, rows in sections.items():
        if name == "Dividends":
            for r in rows[1:]:
                pay, ex, amount, dtype, yld = (r + [""] * 5)[:5]
                # The annual and quarterly exports both carry the dividend table: keep one copy.
                already = con.execute(
                    "SELECT 1 FROM dividends WHERE ticker = ? AND payment_date = ? AND ex_dividend_date = ?",
                    [ticker, pay or None, ex or None],
                ).fetchone()
                if already:
                    continue
                con.execute(
                    "INSERT INTO dividends VALUES (?,?,?,?,?,?,?)",
                    [ticker, pay or None, ex or None, to_number(amount), dtype or None, to_number(yld), source_id],
                )
                counts["dividends"] += 1
            continue
        if name not in STATEMENTS and name != "Ratios":
            raise ValueError(f"Unknown section {name!r} in {path.name}")
        header, body = rows[0], rows[1:]
        periods = [parse_period(p) for p in header[1:]]
        for r in body:
            metric = r[0].strip()
            for (ptype, fy, fq, pend), cell in zip(periods, r[1:]):
                value = to_number(cell)
                flag = "missing" if value is None else "ok"
                if name == "Ratios":
                    con.execute(
                        "INSERT INTO provider_ratios VALUES (?,?,?,?,?,?,?,?,?,?)",
                        [ticker, ptype, fy, fq, pend, metric, value, ratio_unit(metric), flag, source_id],
                    )
                    counts["provider_ratios"] += 1
                else:
                    con.execute(
                        "INSERT INTO statement_lines VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        [ticker, ptype, fy, fq, pend, name, metric, value, statement_unit(metric), flag, source_id],
                    )
                    counts["statement_lines"] += 1
    return counts
