"""Scan the notes to the accounts for restatements, prior-period errors and accounting-policy changes (rule A-05). Code only.

A sentence is kept when it says something was restated or corrected, or that a policy changed, and it is not routine standards wording
(OCI items 'that may be reclassified', policy text on how errors 'are corrected'). Each kept sentence carries its page and is a verbatim
sentence of the PDF text (ligature-tolerant matching, as in the auditor's report reader). The scan finds mentions; it does not read their meaning.
IAS 29 hyperinflation restatement of a foreign subsidiary can also match: the quote shows which it is.
"""
import re

import pdfplumber

from fsa.extract.auditor_report import clean, fold
from fsa.extract.pdf_statements import notes_start_page

SENTENCE = re.compile(r"(?<=[.;])\s+(?=[A-Z(])")
STRONG = ["prior period error", "prior-period error", "prior year error", "correction of error", "correction of an error", "before restatement", "after restatement", "as previously reported",
          "previously reported", "previously stated", "restatement of comparative", "restatement of prior", "prior year adjustment", "comparative figures have been restated",
          "comparatives have been restated", "comparative amounts have been restated", "comparatives were restated", "have been restated", "has been restated", "were restated"]
POLICY = ["voluntary change in accounting policy", "voluntary change in accounting policies", "change in accounting policy has been applied retrospectively", "retrospective application of the change in accounting policy"]
INFLATION = ["inflation", "hyperinflation", "ias 29"]
ROUTINE = ["may be reclassified", "will be reclassified", "subsequently reclassified", "be reclassified to profit", "reclassified to profit or loss", "reclassification adjustment",
           "items that will not be", "corrected retrospectively", "applied retrospectively", "not restated", "not be restated", "would be restated", "amendment", "board clarifies",
           "new and amended", "effective for annual", "ias 8", "an entity shall", "financial statements of a foreign operation", "not the correction"]


def _pattern(phrase: str) -> re.Pattern:
    """The phrase as a word-bounded regex that also matches the PDF's lost-ligature character (\x00) in place of ti, tt, fi, fl or ff."""
    out, i = "", 0
    while i < len(phrase):
        two = phrase[i:i + 2]
        if two in ("ti", "tt", "fi", "fl", "ff"):
            out += f"(?:{two}|\x00)"
            i += 2
        else:
            out += re.escape(phrase[i]) + (r"\s?" if phrase[i] != " " and " " not in phrase and i < len(phrase) - 1 else "")   # one-word phrase: tolerate a stray space inside the word
            i += 1
    return re.compile(r"(?<![a-z])" + out + r"(?![a-z])", re.I)


def _has(sentence: str, phrases: list[str]) -> bool:
    return any(_pattern(p).search(sentence) for p in phrases)


def scan(pdf_path, limit: int = 12) -> dict:
    hits, n_pages = [], 0
    with pdfplumber.open(pdf_path) as pdf:
        start = notes_start_page(pdf)
        if start is None:
            return {"status": "not found", "reason": "the start of the notes was not found in the PDF", "evidence": []}
        for i in range(start, len(pdf.pages) + 1):
            text = pdf.pages[i - 1].extract_text() or ""
            n_pages += 1
            for sent in SENTENCE.split(" ".join(text.split())):          # keep \x00 (a lost ligature) for matching; clean it only for display
                if len(sent) < 25 or _has(sent, ROUTINE):
                    continue
                for kind, phrases in (("restatement or error", STRONG), ("policy change", POLICY)):
                    if _has(sent, phrases):
                        if kind == "restatement or error" and _has(sent, INFLATION):
                            kind = "inflation-accounting restatement (IAS 29), noted only"
                        hits.append({"section": "Notes", "page": i, "kind": kind, "quote": clean(sent)[:420]})
                        break
    seen, out = set(), []
    for h in sorted(hits, key=lambda h: (h["kind"] != "restatement or error", h["page"])):
        k = fold(h["quote"])[:80]
        if k not in seen:
            seen.add(k)
            out.append(h)
    noted = [h for h in out if h["kind"].startswith("inflation")]
    real = [h for h in out if not h["kind"].startswith("inflation")]
    return {"status": "ok", "notes_start_page": start, "pages_scanned": n_pages, "mentions_found": len(real), "evidence": real[:limit], "noted_only": noted[:4]}
