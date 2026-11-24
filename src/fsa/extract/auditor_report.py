"""Read the auditor's report on the financial statements, by code.

Finds the report in the annual-report PDF, splits it into its headed sections, and records what it says: the opinion type, any
'material uncertainty related to going concern' section, emphasis-of-matter and other-matter paragraphs, and the key audit matters.
Every statement carries the page and a quote. A quote must be found again in the PDF text by code before it is kept (project rule 4).
Only a model (Haiku) lists the KAM titles, and every title it gives is dropped unless its quote is found in the report text.

PDF text from these filings often loses letters ('posi on', 'Ma ers': the 'ti' and 'tt' ligatures). So matching ignores case,
punctuation, digits and the letters t, i, f, l (`fold`), while quotes are kept as printed.
"""
import json
import re

import pdfplumber

TIFL = str.maketrans("", "", "tifl")
SENTENCE = re.compile(r"(?<=[.;:])\s+")
FIRMS = ["KPMG", "Deloitte", "PricewaterhouseCoopers", "PwC", "Ernst & Young", "Grant Thornton", "BDO", "Baker Tilly", "SIAO", "Akintola Williams", "Crowe", "Mazars"]

HEADINGS = {                                         # section -> heading phrases (matched on the folded line start)
    "opinion": ["opinion"], "qualified_opinion": ["qualified opinion"], "adverse_opinion": ["adverse opinion"], "disclaimer": ["disclaimer of opinion"],
    "basis": ["basis for opinion", "basis for qualified opinion", "basis for adverse opinion", "basis for disclaimer of opinion"],
    "material_uncertainty": ["material uncertainty related to going concern"],
    "kam": ["key audit matters", "key audit matter"], "emphasis": ["emphasis of matter"], "other_matter": ["other matter", "other matters"],
    "other_information": ["other information"], "directors": ["responsibilities of the directors", "responsibilities of directors", "responsibilities of management", "responsibilities of the board"],
    "auditor_resp": ["auditors responsibilities", "auditor's responsibilities", "our responsibilities"], "legal": ["report on other legal and regulatory requirements"],
}
MODIFIED = {"qualified": ["qualified opinion", "except for the effects", "except for the possible effects"], "adverse": ["adverse opinion", "do not give a true and fair view", "do not present fairly"],
            "disclaimer": ["disclaimer of opinion", "do not express an opinion", "not able to obtain sufficient appropriate audit evidence to provide a basis"]}
UNMODIFIED = ["give a true and fair view", "presents fairly", "present fairly"]


def fold(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower().replace("’", "'")).translate(TIFL)


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("\x00", "?")).strip()          # \x00 marks a letter the PDF lost (a ligature)


def read_pages(pdf_path) -> dict[int, str]:
    """page number -> text, from the opinion page to the end of the audit report.

    The end is the first page with the signature block (the firm's 'Chartered Accountants' line or an FRC number), or the page before a
    separate report starts (the limited-assurance report on internal control), or after at most 8 pages."""
    pages, start = {}, None
    with pdfplumber.open(pdf_path) as pdf:
        for i, pg in enumerate(pdf.pages, 1):
            t = pg.extract_text() or ""
            f = fold(t)
            if start is None:
                if fold("basis for opinion") in f and fold("we have audited") in f:
                    start = i
                else:
                    continue
            elif any(len(ln.strip()) < 70 and "assurancereport" in re.sub(r"[\s\x00]+", "", ln.lower()) for ln in t.split("\n")):
                break                                              # a different report (limited assurance on internal control) begins on this page
            pages[i] = t
            lines = [re.sub(r"\s+", "", ln.lower()) for ln in t.split("\n")]
            if any(ln == "charteredaccountants" or ln.startswith("frc/") for ln in lines) or i >= start + 8:
                break
    return pages


def heading_of(line: str) -> str | None:
    f, n = fold(line), len(line.strip())
    if n < 4 or n > 90 or line.rstrip().endswith((",", ";")):
        return None
    for section, phrases in HEADINGS.items():
        for ph in phrases:
            fp = fold(ph)
            if section == "opinion":
                if f == fp:
                    return section
            elif f.startswith(fp) and n <= len(ph) + 45:
                return section
    return None


def split_sections(pages: dict[int, str]) -> list[dict]:
    secs, cur = [], {"section": "preamble", "lines": []}
    for p, text in pages.items():
        for ln in text.split("\n"):
            h = heading_of(ln)
            if h:
                secs.append(cur)
                cur = {"section": h, "heading": clean(ln), "page": p, "lines": []}
            elif ln.strip():
                cur["lines"].append((p, ln))
    secs.append(cur)
    for s in secs:
        s["text"] = clean(" ".join(l for _, l in s["lines"]))
        s["page"] = s.get("page") or (s["lines"][0][0] if s["lines"] else None)
    return [s for s in secs if s["text"] or s["section"] != "preamble"]


def sentence_with(text: str, phrases: list[str]) -> str | None:
    for sent in SENTENCE.split(text):
        if any(fold(p) in fold(sent) for p in phrases):
            return sent.strip()
    return None


def first_sentences(text: str, n: int = 2) -> str:
    return " ".join(SENTENCE.split(text)[:n]).strip()


def verify_quote(quote: str, pages: dict[int, str]) -> bool:
    """The quote must be found again in the report text (ligature- and punctuation-tolerant)."""
    q = fold(quote)
    return len(q) >= 8 and q in fold(" ".join(pages.values()))


def analyse(pages: dict[int, str]) -> dict:
    secs = split_sections(pages)
    by = lambda *names: [s for s in secs if s["section"] in names]
    op_text = " ".join(s["text"] for s in by("opinion", "qualified_opinion", "adverse_opinion", "disclaimer", "basis"))
    head_hit = {k: v for k, v in (("qualified", by("qualified_opinion")), ("adverse", by("adverse_opinion")), ("disclaimer", by("disclaimer"))) if v}
    kind, quote, page = "unclear", None, None
    for k in ("disclaimer", "adverse", "qualified"):
        sent = sentence_with(op_text, MODIFIED[k])
        if k in head_hit or sent:
            kind = k
            quote = sent or (head_hit[k][0]["heading"] if k in head_hit else None)
            break
    else:
        sent = sentence_with(" ".join(s["text"] for s in by("opinion")) or op_text, UNMODIFIED)
        if sent:
            kind, quote = "unmodified", sent
    if quote:
        page = next((s["page"] for s in by("opinion", "qualified_opinion", "adverse_opinion", "disclaimer", "basis") if fold(quote) in fold(s["text"] + s.get("heading", ""))), None)
    mu = by("material_uncertainty")
    eom = by("emphasis")
    om = by("other_matter")
    kam = by("kam")
    gc_in_eom = [s for s in eom if "goingconcern" in fold(s["text"]) .replace("gong", "going")] if eom else []
    joined = " ".join(p for p in pages.values())
    flat = re.sub(r"\s+", "", joined.lower())
    firm = next((f for f in FIRMS if re.sub(r"\s+", "", f.lower()) in flat), None)
    evid = lambda ss: [{"section": s["heading"], "page": s["page"], "quote": first_sentences(s["text"])} for s in ss if s["text"]]
    return {
        "pages": [min(pages), max(pages)], "firm": firm,
        "opinion": {"type": kind, "quote": quote, "page": page},
        "going_concern": {"material_uncertainty_section": bool(mu or gc_in_eom), "evidence": evid(mu or gc_in_eom)},
        "emphasis_of_matter": evid(eom), "other_matter": evid(om),
        "key_audit_matters": {"section_present": bool(kam), "page": kam[0]["page"] if kam else None, "titles": []},
        "signature_found": any(re.sub(r"\s+", "", ln.lower()) == "charteredaccountants" or re.sub(r"\s+", "", ln.lower()).startswith("frc/") for ln in joined.split("\n")),
        "damaged_ligatures": "\x00" in joined or "(cid:" in joined,
        "sections_found": [s["section"] for s in secs if s["section"] != "preamble"],
        "kam_text": " ".join(k["text"] for k in kam),
    }


KAM_SYSTEM = ("You list the key audit matters from an auditor's report. Return JSON only: [{\"title\": \"...\"}]. "
              "title must be the matter's name copied EXACTLY, word for word, as the auditor prints it in the text (a short heading, usually after a number such as '1.'). "
              "Do not tidy, shorten, reorder or rename it. Never invent a matter; if you cannot copy the printed words, leave the matter out.")


def kam_titles(report: dict, pages: dict[int, str], model: str) -> dict:
    """Haiku copies each KAM heading; code keeps a title only if those exact words are found again in the report text. One retry for rejects."""
    from fsa.map.model_proposer import ask, verify_model
    text, used, cost, kept, rejected = report["kam_text"][:14000], set(), 0.0, [], []
    prompt = "Key audit matters section of the report:\n\n" + text + "\n\nReturn the JSON array."
    for attempt in (1, 2):
        ans = ask(prompt, model, system=KAM_SYSTEM)
        if ans.is_error:
            raise RuntimeError(f"the model call failed: {ans.error_text}")
        used |= verify_model(ans, model)
        cost += ans.cost_usd or 0.0
        m = re.search(r"\[.*\]", ans.text, re.S)
        try:
            items = json.loads(m.group(0)) if m else []
        except json.JSONDecodeError:
            items = []
        rejected = []
        for it in items:
            title = clean(str(it.get("title", "")))
            if title and verify_quote(title, pages):
                if all(fold(title) != fold(k["title"]) for k in kept):
                    q = fold(title)
                    kept.append({"title": title, "quote": title, "page": next((n for n, t in pages.items() if q in fold(t)), None)})
            elif title:
                rejected.append(title)
        if not rejected:
            break
        prompt += "\n\nThese titles were not found word for word in the text, so code rejected them: " + "; ".join(rejected) + ". Copy the printed words exactly, or leave the matter out. Return the full JSON array again."
    return {"titles": kept, "dropped_unverified": len(rejected), "model": sorted(used), "cost_usd": cost}


def red_flags(report: dict | None) -> float | None:
    """1.0 if the opinion is modified or a going-concern uncertainty is reported; 0.0 if neither; None if the report was not read."""
    if not report or report["opinion"]["type"] == "unclear":
        return None
    return float(report["opinion"]["type"] != "unmodified" or report["going_concern"]["material_uncertainty_section"])


def to_markdown(r: dict, ticker: str, fy: int) -> str:
    o = r["opinion"]
    L = [f"# Auditor's report: {ticker} FY{fy}", "", f"> Read by code from pages {r['pages'][0]}-{r['pages'][1]} of the annual report PDF. Quotes are verified against the PDF text. Educational analysis, not investment advice.", "",
         f"- **Firm:** {r['firm'] or 'not identified'}", f"- **Opinion:** {o['type']}" + (f" (p.{o['page']}): “{o['quote']}”" if o["quote"] else ""),
         f"- **Going-concern material uncertainty section:** {'yes' if r['going_concern']['material_uncertainty_section'] else 'none found'}"]
    for e in r["going_concern"]["evidence"]:
        L.append(f"  - p.{e['page']} '{e['section']}': “{e['quote']}”")
    for label, key in (("Emphasis of matter", "emphasis_of_matter"), ("Other matter", "other_matter")):
        L.append(f"- **{label}:** {'none found' if not r[key] else ''}")
        L += [f"  - p.{e['page']} '{e['section']}': “{e['quote']}”" for e in r[key]]
    k = r["key_audit_matters"]
    L.append(f"- **Key audit matters:** " + ("section found" + (f" (p.{k['page']})" if k["page"] else "") if k["section_present"] else "none found"))
    L += [f"  - {t['title']}  (“{t['quote']}”)" for t in k["titles"]]
    L.append("- **End of report:** " + ("signature block found" if r.get("signature_found") else "signature block NOT found; the PDF text for the end of the report may be incomplete"))
    L.append(f"- **Text quality:** " + ("damaged letters (missing ligatures) in the PDF text; matching tolerates this" if r["damaged_ligatures"] else "no ligature damage detected"))
    return "\n".join(L) + "\n"
