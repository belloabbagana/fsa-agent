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
