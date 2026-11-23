"""Pull the primary financial statements out of an annual-report PDF. Code only, no model.

Each statement page is a table: label, optional note reference, then value columns (typically Group
this year / last year, Company this year / last year; some reports add a restated opening column).
A value in brackets is negative. A dash means the filing prints nil: stored as 0 and flagged
'reported_nil'. A cell left empty in the filing is stored as NULL and flagged 'blank_in_filing'
(never as zero). Anything else we cannot read cleanly is flagged, never guessed.

Lines are rebuilt from the position of each word on the page, not from the PDF's text order.
 * Some reports have stray spaces inside numbers ("65 ,032,020"): fragments of one number touch each
   other (gap of about 0 points), whereas words in a label are about 2 points apart, columns 20+.
 * Numbers are right-aligned, so each number is assigned to the column whose right edge it lines up
   with. That keeps a blank cell blank instead of shifting the other numbers one column left.
"""
import re
import statistics
from dataclasses import dataclass, field
from datetime import date

import pdfplumber

DASHES = {"-", "‑", "–", "—"}          # hyphen, non-breaking hyphen, en dash, em dash
NUMBER = re.compile(r"^\(?\d[\d,]*(\.\d+)?\)?$")
NUMERICISH = re.compile(r"^[\d,.()\-‑–—]+$")
NOTE_REF = re.compile(r"^\d{1,2}(\.\d{1,2}){0,3}$")
SLASH_DATE = re.compile(r"\b(\d{2})/(\d{2})/(\d{4}|\d{2})\b")
MONTH_DATE = re.compile(r"(\d{1,2})(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?(\d{4})")
MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), start=1)}
END_MARK = "the accompanying notes"
ROW_TOL = 3.5            # points: words whose tops differ by less than this are on one line
FUSE_GAP = 1.0           # points: numeric words closer than this are fragments of one number

STOP_AT_LAST = {"financial_position", "cash_flows"}
STATEMENT_NAMES = ("profit_or_loss", "comprehensive_income", "financial_position", "cash_flows")


def squash(text: str) -> str:
    """Lower-case with spaces and the marks of broken ligatures removed (for matching only)."""
    return re.sub(r"[\s\x00·]", "", text).lower()


def norm_label(text: str) -> str:
    """Matching form of a label. Some PDFs drop ligatures (ti, tt, fi...) or scatter spaces, so both the
    printed label and the rule's label go through this before they are compared."""
    s = squash(text)
    for lig in ("ffi", "ffl", "ti", "tt", "ff", "fi", "fl", "ft", "tf"):
        s = s.replace(lig, "")
    return s


@dataclass
class Line:
    seq: int
    section: str
    label: str
    note_ref: str | None
    values: list          # (value | None, flag) per column
    page: int
    raw: str
    problems: list = field(default_factory=list)


# ---------------------------------------------------------------- reading rows from a page
def page_rows(page) -> list[tuple[str, list[dict]]]:
    """Rebuild the visual rows of a page: (text, tokens) with tokens = [{text, x0, x1}], left to right."""
    words = page.extract_words(x_tolerance=1.5, y_tolerance=2, keep_blank_chars=False, extra_attrs=["upright"])
    words = [w for w in words if w["upright"]]            # drop rotated margin text (it would cut into table rows)
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if rows and abs(rows[-1][0]["top"] - w["top"]) <= ROW_TOL:
            rows[-1].append(w)
        else:
            rows.append([w])
    out = []
    for row in rows:
        row.sort(key=lambda w: w["x0"])
        toks: list[dict] = []
        for w in row:
            t = w["text"]
            prev = toks[-1] if toks else None
            if prev and NUMERICISH.match(t) and NUMERICISH.match(prev["text"]) and (
                    w["x0"] - prev["x1"] < FUSE_GAP or (t == ")" and prev["text"].startswith("(") and ")" not in prev["text"])):
                toks[-1] = {"text": prev["text"] + t, "x0": prev["x0"], "x1": w["x1"]}
            else:
                toks.append(dict(text=t, x0=w["x0"], x1=w["x1"]))
        out.append((" ".join(t["text"] for t in toks), toks))
    return out


def page_text_lines(page) -> list[str]:
    return [text for text, _ in page_rows(page)]


def is_value_token(tok: str) -> bool:
    return tok in DASHES or bool(NUMBER.match(tok))


def parse_value(tok: str):
    if tok in DASHES:
        return 0.0, "reported_nil"
    neg = tok.startswith("(") or tok.endswith(")")        # a stray space can leave only one of the two brackets
    num = float(tok.strip("()").replace(",", ""))
    return (-num if neg else num), "ok"


def _fix_multi_note(rest: list[str], note: str | None):
    while note and len(rest) > 1 and (rest[-1] in {"&", ","} or re.fullmatch(r"\d{1,2}(\.\d{1,2}){0,3},?", rest[-1])):
        note = rest.pop() + " " + note                   # a note reference printed as '15, 16 & 17'
    return note.strip() if note else note


def _eps_like(label: str) -> bool:
    low = label.lower()
    return any(k in low for k in ("per share", "(naira)", "(kobo)", "basic", "diluted"))


def parse_line(text: str, n_cols: int = 4):
    """Text-only reading (no positions): the last n_cols numeric tokens are the values.
    Returns (label, note_ref, values, problems), or None when the line has no numeric tail."""
    toks = text.split()
    run = 0
    while run < len(toks) and is_value_token(toks[-1 - run]):
        run += 1
    if run == 0:
        return None
    if run < n_cols:
        return text.strip(), None, [], [f"only {run} numeric columns"]
    vals = toks[len(toks) - n_cols:]
    rest = toks[: len(toks) - n_cols]
    problems, note = [], None
    if rest and rest[-1] in DASHES:
        rest.pop()                                       # a dash in the notes column: no note
    elif rest and NOTE_REF.match(rest[-1]):
        note = rest.pop()
    elif rest and is_value_token(rest[-1]):
        problems.append("extra numeric token before values")
    note = _fix_multi_note(rest, note)
    label = " ".join(rest)
    values = [parse_value(v) for v in vals]
    if not _eps_like(label) and any(re.fullmatch(r"\d{1,2}\.\d{1,2}", v) for v in vals):
        problems.append("a note reference sits in a value column: columns cannot be placed")
        values = [(None, "unreadable")] * n_cols
    return label, note, values, problems


def column_edges(rows, n_cols: int):
    """Right edge of each value column, from the rows that have a number in every column."""
    xs = [[] for _ in range(n_cols)]
    for _, toks in rows:
        tail = []
        for t in reversed(toks):
            if is_value_token(t["text"]):
                tail.append(t)
            else:
                break
        tail.reverse()
        if len(tail) == n_cols + 1 and NOTE_REF.match(tail[0]["text"]):
            tail = tail[1:]
        if len(tail) == n_cols and all(t["text"] not in DASHES for t in tail):
            for i, t in enumerate(tail):
                xs[i].append(t["x1"])
    if min(len(x) for x in xs) < 3:
        return None
    return [statistics.median(x) for x in xs]


def parse_row(text: str, toks: list[dict] | None, n_cols: int, edges):
    """Position-based reading when column edges are known, else the text-only reading."""
    if not edges or not toks:
        return parse_line(text, n_cols)
    tail = []
    for t in reversed(toks):
        if is_value_token(t["text"]):
            tail.append(t)
        else:
            break
    tail.reverse()
    if not tail:
        return None
    pitch = statistics.median([b - a for a, b in zip(edges, edges[1:])]) if n_cols > 1 else 60.0
    cols: list[dict | None] = [None] * n_cols
    problems, note = [], None
    n_tail = len(tail)
    for t in tail:
        if t["x1"] < edges[0] - 0.55 * pitch:             # left of the first column: the notes column
            if note is None and NOTE_REF.match(t["text"]):
                note = t["text"]
            else:
                problems.append(f"stray number {t['text']!r} left of the value columns")
            continue
        k = min(range(n_cols), key=lambda j: abs(t["x1"] - edges[j]))
        if abs(t["x1"] - edges[k]) > 0.6 * pitch:
            problems.append(f"number {t['text']!r} does not line up with a column")
            continue
        if cols[k] is not None:
            problems.append(f"two numbers in one column near {t['text']!r}")
            continue
        cols[k] = t
    if all(c is None for c in cols):
        return None                                       # no number sits in a value column: not a data row (e.g. a title with a year)
    first_label_tok = len(toks) - n_tail
    rest = [t["text"] for t in toks[:first_label_tok]]
    while rest and rest[-1] == "(":
        rest.pop()                                        # an opening bracket separated from its number
    if note is None and len(rest) > 1 and re.fullmatch(r"\d{1,2}(\.\d{1,2}){1,3}", rest[-1]):
        note = rest.pop()                                 # multi-level note such as 30.6.1 (not read as a number)
    note = _fix_multi_note(rest, note)
    label = " ".join(rest)
    if problems:
        values = [(parse_value(c["text"]) if c else (None, "unreadable")) for c in cols]
        return label, note, values, problems
    values = [parse_value(c["text"]) if c else (None, "blank_in_filing") for c in cols]
    return label, note, values, problems


# ---------------------------------------------------------------- header: columns, dates, unit
def _dates_in(line: str):
    found = [date(int(y) + (2000 if len(y) == 2 else 0), int(m), int(d)) for d, m, y in SLASH_DATE.findall(line)]
    if len(found) < 2:
        found = [date(int(y), MONTHS[mon], int(d)) for d, mon, y in MONTH_DATE.findall(squash(line))]
    return found


def parse_header(lines: list[str], fiscal_year: int | None):
    """-> (columns, unit). columns = [(entity, period_end), ...] in printed order.
    The column header is the line carrying the most dates (title lines mention one date at most)."""
    head = lines[:16]
    joined = "\n".join(head)
    dates = max((_dates_in(ln) for ln in head), key=len, default=[])
    if len(dates) < 2:
        m = re.search(r"31 December (\d{4})", joined)
        y = int(m.group(1)) if m else fiscal_year
        dates = [date(y, 12, 31), date(y - 1, 12, 31)] * 2 if y else []
    # the company block starts where the first date repeats
    split = next((i for i in range(1, len(dates)) if dates[i] == dates[0]), len(dates))
    columns = [("group" if i < split else "company", d) for i, d in enumerate(dates)]
    sq = squash(joined)
    unit = "unknown"
    if re.search(r"[n₦]['’‘`]?million", sq):
        unit = "NGN_million"
    elif re.search(r"[n₦]['’‘`]?000", sq):
        unit = "NGN_thousand"
    return columns, unit


UNIT_HEADER = re.compile(r"[n₦]['’‘`](?:000|00|million)")          # the 'N’000' / '₦'million' unit line of a table header
SUBTOTAL_WORDS = re.compile(r"^(total|net)\b", re.IGNORECASE)


def parse_statement_lines(rows, page: int, n_cols: int, start_seq: int = 0, section: str = "", stop_after: str | None = None, edges=None):
    """rows: list of text strings, or (text, tokens) pairs."""
    out, pending, seq = [], None, start_seq
    for row in rows:
        raw, toks = (row, None) if isinstance(row, str) else row
        low = raw.lower()
        if low.startswith(END_MARK):
            break
        sq = squash(raw)
        if (not raw.strip() or SLASH_DATE.search(raw) or MONTH_DATE.search(sq) or "yearended" in sq or low.startswith("notes")
                or "₦" in raw or UNIT_HEADER.search(sq) or sq.startswith(("fortheyear", "asat", "annualreport", "*")) or sq in {"groupcompany", "group", "company", "restated*restated*"}):
            continue                                      # titles and column headers, even when they hold numbers such as a year
        parsed = parse_row(raw, toks, n_cols, edges)
        if parsed is None:
            if raw[:1].islower() and out:
                out[-1].label += " " + raw.strip()        # wrapped second line below the numbers
                continue
            if pending is not None:
                section = pending
                pending = None
            if len(raw) < 60 and not raw.isupper():
                pending = raw.strip()                     # a heading, unless the next numeric line continues it
            continue
        label, note, values, problems = parsed
        if not values:
            continue
        if pending is not None:
            if label[:1].islower() or (label == "" and note):
                label = (pending + " " + label).strip()   # wrapped first line above the numbers
            elif label == "" and SUBTOTAL_WORDS.match(pending):
                label = pending                           # 'Total equity' printed on the line above its numbers
            else:
                section = pending
            pending = None
        seq += 1
        out.append(Line(seq, section, label, note, values, page, raw, problems))
        if stop_after and stop_after in norm_label(label):
            break                                         # last line of the statement: what follows is not part of it
    return out, section


def parse_statement_page(text: str, page: int, fiscal_year: int | None = None):
    """Parse a page given as plain text (used by the tests): -> (dates, unit, lines)."""
    lines = text.splitlines()
    columns, unit = parse_header(lines, fiscal_year)
    parsed, _ = parse_statement_lines(lines, page, len(columns) or 4)
    return [d for _, d in columns], unit, parsed


# ---------------------------------------------------------------- finding the statements
_RAW_MARKERS = {   # all 'needed' phrases must appear, no 'forbidden' phrase may (matched on normalised text)
    "profit_or_loss": (["profitbeforetax", "revenue", "grossprofit"], ["investingactivities"]),
    "comprehensive_income": (["othercomprehensiveincome", "totalcomprehensiveincome"],
                             ["sharecapital", "retainedearnings", "profitbeforetax", "totalassets", "investingactivities"]),
    "financial_position": (["totalassets", "totalequity"], ["investingactivities"]),
    "cash_flows": (["operatingactivities", "investingactivities", "financingactivities"], []),
}
MARKERS = {k: ([norm_label(x) for x in n], [norm_label(x) for x in f]) for k, (n, f) in _RAW_MARKERS.items()}
LAST_LINE = {    # a statement is complete once this phrase has been seen; otherwise it continues on the next page
    "profit_or_loss": norm_label("profitfortheyear"),
    "comprehensive_income": norm_label("totalcomprehensiveincome"),
    "financial_position": norm_label("totalequityandliabilities"),
    "cash_flows": norm_label("endofyear"),
}


def _squashed_page(pg) -> str:
    return norm_label(pg.extract_text() or "")


def notes_start_page(pdf) -> int | None:
    """First page that opens the 'Notes to the ... financial statements' (note 1, General information)."""
    for i, pg in enumerate(pdf.pages, start=1):
        text = pg.extract_text() or ""
        head, body = norm_label(text[:300]), norm_label(text[:800])
        if norm_label("notesto") in head and norm_label("financialstatements") in head and re.search(r"1\.?general", body):
            return i
    return None


def locate_statement_pages(pdf) -> dict:
    """Find each statement by its content, within the dozen pages before the notes begin.
    Titles are not relied on, and a statement may run over a page break: the first marker must be on
    the page, the others on that page or the next."""
    end = notes_start_page(pdf)
    if end is None:
        return {}
    squashed = {i: _squashed_page(pdf.pages[i - 1]) for i in range(max(1, end - 15), end + 1)}
    found = {}
    for i in range(max(1, end - 14), end):
        here, both = squashed[i], squashed[i] + squashed.get(i + 1, "")
        for name, (needed, forbidden) in MARKERS.items():
            if name in found or needed[0] not in here or any(k in here for k in forbidden):
                continue
            if not all(k in both for k in needed):
                continue
            rows = page_rows(pdf.pages[i - 1])
            if sum(1 for t, _ in rows if (p := parse_line(t, 2)) and p[2]) >= 4:
                found[name] = i
    return found


def extract_statements(pdf_path, fiscal_year: int | None = None) -> dict:
    """-> {statement: (first_page, columns, unit, lines)} ; columns = [(entity, period_end), ...]"""
    out = {}
    with pdfplumber.open(pdf_path) as pdf:
        pages = locate_statement_pages(pdf)
        end = notes_start_page(pdf) or len(pdf.pages) + 1
        for name, first in pages.items():
            lines_all, columns, unit, section, seq, edges = [], None, "unknown", "", 0, None
            page = first
            while page < end:
                rows = page_rows(pdf.pages[page - 1])
                if columns is None:
                    columns, unit = parse_header([t for t, _ in rows], fiscal_year)
                n_cols = len(columns) or 4
                edges = column_edges(rows, n_cols) or edges
                parsed, section = parse_statement_lines(rows, page, n_cols, seq, section,
                                                        LAST_LINE[name] if name in STOP_AT_LAST else None, edges)
                lines_all += parsed
                seq = lines_all[-1].seq if lines_all else seq
                if LAST_LINE[name] in norm_label("".join(t for t, _ in rows)) or page - first >= 2:
                    break
                page += 1                                  # statement continues on the next page
            out[name] = (first, columns, unit, lines_all)
    return out
