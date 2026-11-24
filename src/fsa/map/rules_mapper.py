"""Rule-based baseline mapping: printed line -> chart-of-accounts code. No model.

A label is matched to the chart's printed-label examples (ignoring spacing and broken ligatures). Where
the same label belongs to more than one account (current vs non-current 'Borrowings'), the section
heading decides. Anything still ambiguous or unknown is left for the model and the analyst.
"""
from fsa.extract.pdf_statements import norm_label
from fsa.map.chart import STATEMENT_GROUPS, label_index

MEMO_HINT = "memo"


def _side(section: str) -> str | None:
    s = norm_label(section).replace("-", "")          # "Non-current" must not be read as "current"
    if "noncurrent" in s:
        return "NC"
    if "current" in s:
        return "C"
    return None


def _is_oci_context(ln) -> bool:
    """A line sits in the comprehensive-income part when its page is the OCI page or its heading says so."""
    return ln["statement"] == "comprehensive_income" or "comprehensive" in norm_label(ln["section"])


def _split_by_statement(chart: dict, codes: list, ln: dict):
    """Same label, one account in profit or loss and one in OCI (e.g. 'Owners of the Company'): context decides."""
    kinds = {a["code"]: a["statement"] for a in chart["accounts"] if a["code"] in codes}
    pl = [c for c in codes if kinds[c] == "profit_or_loss"]
    oci = [c for c in codes if kinds[c] == "other_comprehensive_income"]
    if len(pl) == 1 and len(oci) == 1 and len(codes) == 2:
        if _is_oci_context(ln):
            return oci[0], "same label as the profit split; this line sits in the comprehensive-income part"
        return pl[0], "same label as the comprehensive-income split; this line sits in the profit part"
    return None


def propose(chart: dict, lines) -> list[dict]:
    """lines: iterable of dicts {statement, seq, section, label}. Returns one proposal per line."""
    idx = label_index(chart)
    out = []
    for ln in lines:
        key = norm_label(ln["label"])
        allowed = STATEMENT_GROUPS.get(ln["statement"], set())
        codes = sorted({code for st, code in idx.get(key, set()) if st in allowed})
        prop = {"statement": ln["statement"], "seq": ln["seq"], "section": ln["section"], "label": ln["label"],
                "proposer": "rule", "code": None, "confidence": None, "reason": ""}
        if not key:
            prop["reason"] = "no printed label (unlabelled subtotal)"
        elif len(codes) == 1:
            prop.update(code=codes[0], confidence="exact", reason="label matches the chart")
        elif len(codes) > 1 and _split_by_statement(chart, codes, ln):
            code, why = _split_by_statement(chart, codes, ln)
            prop.update(code=code, confidence="section", reason=why)
        elif len(codes) > 1:
            side = _side(ln["section"])
            pick = [c for c in codes if side and c.endswith("_" + side)]
            if len(pick) == 1:
                prop.update(code=pick[0], confidence="section", reason=f"label is shared by {codes}; the section heading picks {pick[0]}")
            else:
                prop["reason"] = f"ambiguous between {codes}; section {ln['section']!r} does not decide"
        else:
            prop["reason"] = "label not in the chart"
        out.append(prop)
    return out
