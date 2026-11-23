"""Pull the primary financial statements out of an annual-report PDF. Code only, no model.

Each statement page is a table of: label, optional note reference, then four value columns
(Group this year, Group last year, Company this year, Company last year). A value in brackets is
negative. A dash means the filing reports nil: we store 0 and flag it 'reported_nil' (a printed nil,
not a missing value). Anything we cannot read cleanly is flagged, never guessed.
"""
import re
from dataclasses import dataclass, field
from datetime import date

import pdfplumber

STATEMENT_HEADINGS = {
    "profit_or_loss": r"statements? of profit or loss",
    "comprehensive_income": r"statements? of comprehensive income",
    "financial_position": r"statements? of financial position",
    "cash_flows": r"statements? of cash ?flows?",
}
DASHES = {"-", "‑", "–", "—"}          # hyphen, non-breaking hyphen, en dash, em dash
NUMBER = re.compile(r"^\(?\d[\d,]*(\.\d+)?\)?$")
NOTE_REF = re.compile(r"^\d{1,2}(\.\d{1,2}){0,3}$")
DATE = re.compile(r"\b(\d{2})/(\d{2})/(\d{4}|\d{2})\b")
END_MARK = "the accompanying notes"
N_COLS = 4


@dataclass
class Line:
    seq: int
    section: str
    label: str
    note_ref: str | None
    values: list          # list of (value | None, flag)
    page: int
    raw: str
    problems: list = field(default_factory=list)


def is_value_token(tok: str) -> bool:
    return tok in DASHES or bool(NUMBER.match(tok))


def parse_value(tok: str):
    if tok in DASHES:
        return 0.0, "reported_nil"
    neg = tok.startswith("(") and tok.endswith(")")
    num = float(tok.strip("()").replace(",", ""))
    return (-num if neg else num), "ok"


def parse_line(text: str):
    """Return (label, note_ref, values, problems) or None when the line has no value columns."""
    toks = text.split()
    run = 0
    while run < len(toks) and is_value_token(toks[-1 - run]):
        run += 1
    if run == 0:
        return None
    problems = []
    if run < N_COLS:
        return text.strip(), None, [], [f"only {run} numeric columns"]
    vals = toks[len(toks) - N_COLS:]
    rest = toks[: len(toks) - N_COLS]
    note = None
    if rest and NOTE_REF.match(rest[-1]) and len(toks) - N_COLS >= 1:
        # a trailing short number before the 4 values is the note reference
        if len(rest) == 1 or not is_value_token(rest[-2]) or True:
            note = rest.pop()
    elif rest and is_value_token(rest[-1]):
        problems.append("extra numeric token before values")
    while note and len(rest) > 1 and (rest[-1] in {"&", ","} or re.fullmatch(r"\d{1,2}(\.\d{1,2}){0,3},?", rest[-1])):
        note = rest.pop() + " " + note                 # a note reference printed as '15, 16 & 17'
    label = " ".join(rest)
    return label, (note.strip() if note else note), [parse_value(v) for v in vals], problems


def column_dates(page_text: str, fiscal_year: int | None = None):
    """Four column dates from the header, e.g. 31/12/2025 31/12/2024 31/12/2025 31/12/2024."""
    found = []
    for line in page_text.splitlines()[:12]:
        found += [date(int(y) + (2000 if len(y) == 2 else 0), int(m), int(d)) for d, m, y in DATE.findall(line)]
        if len(found) >= N_COLS:
            break
    if len(found) < N_COLS:
        m = re.search(r"31 December (\d{4})", page_text[:300])
        y = int(m.group(1)) if m else fiscal_year        # the title, else the report's own year
        if y:
            found = [date(y, 12, 31), date(y - 1, 12, 31)] * 2
    return found[:N_COLS]


def parse_statement_page(text: str, page: int, fiscal_year: int | None = None):
    dates = column_dates(text, fiscal_year)
    unit = "NGN_million" if re.search(r"[₦N]['’]\s?million", text[:600]) else "unknown"
    lines, section, seq, pending = [], "", 0, None
    for raw in text.splitlines():
        low = raw.lower()
        if low.startswith(END_MARK):
            break
        parsed = parse_line(raw)
        if parsed is None:
            if not raw.strip() or DATE.search(raw) or "year ended" in low or low.startswith("notes") or "₦" in raw:
                continue                                  # column headers
            if raw[:1].islower() and lines:
                lines[-1].label += " " + raw.strip()      # wrapped second line below the numbers
                continue
            if pending is not None:
                section = pending                         # two text lines in a row: the first was a heading
                pending = None
            if len(raw) < 60 and not raw.isupper():
                pending = raw.strip()                     # a heading, unless the next numeric line continues it
            continue
        label, note, values, problems = parsed
        if not values:
            continue
        if pending is not None:
            if label[:1].islower():
                label = pending + " " + label             # wrapped first line above the numbers
            else:
                section = pending
            pending = None
        seq += 1
        lines.append(Line(seq, section, label, note, values, page, raw, problems))
    return dates, unit, lines


MARKERS = {   # (all of these must appear, none of the forbidden ones may appear)
    "profit_or_loss": (["profit before tax", "revenue", "gross profit"], ["cash flows from operating"]),
    "comprehensive_income": (["other comprehensive income", "total comprehensive income"],
                             ["share capital", "retained earnings", "profit before tax", "total assets", "cash flows from"]),
    "financial_position": (["total assets", "total liabilities", "total equity"], ["cash flows from operating"]),
    "cash_flows": (["cash flows from operating activities", "net cash"], []),
}


def _numeric_rows(text: str) -> int:
    return sum(1 for ln in text.splitlines() if (p := parse_line(ln)) and p[2])


def notes_start_page(pdf) -> int | None:
    """First page that opens the 'Notes to the ... financial statements' (note 1, General information)."""
    for i, pg in enumerate(pdf.pages, start=1):
        text = pg.extract_text() or ""
        head = text[:260].lower()
        if "notes to the" in head and "financial statements" in head and re.search(r"\b1\.\s*general", text[:700].lower()):
            return i
    return None


def locate_statement_pages(pdf) -> dict:
    """Find each statement by its content, within the dozen pages before the notes begin.
    Titles are not relied on: some reports print the title on the previous page or not at all."""
    end = notes_start_page(pdf)
    if end is None:
        return {}
    found = {}
    for i in range(max(1, end - 14), end):
        text = pdf.pages[i - 1].extract_text() or ""
        low = text.lower()
        for name, (needed, forbidden) in MARKERS.items():
            if name in found or not all(k in low for k in needed) or any(k in low for k in forbidden):
                continue
            if _numeric_rows(text) >= 4:
                found[name] = i
    return found


def extract_statements(pdf_path, fiscal_year: int | None = None) -> dict:
    out = {}
    with pdfplumber.open(pdf_path) as pdf:
        pages = locate_statement_pages(pdf)
        for name, page in pages.items():
            text = pdf.pages[page - 1].extract_text() or ""
            out[name] = (page, *parse_statement_page(text, page, fiscal_year))
    return out
