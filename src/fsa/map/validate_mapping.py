"""Arithmetic check of a mapping: do the mapped lines add up to the totals the company printed?

If every line sits in the right account, each printed subtotal (gross profit, total assets, net cash from
operations...) equals the chart's sum of its parts. A wrong mapping breaks a sum, so this catches errors
without trusting any model. A difference can also be real (e.g. a company that prints a line in another
place than the standardised layout); those are reported, not hidden.
"""


def _by_code(chart):
    return {a["code"]: a for a in chart["accounts"]}


def account_totals(chart, mapping: dict, values: dict) -> dict:
    """code -> sum of printed values of the lines mapped to that line-account (memo accounts excluded)."""
    accts = _by_code(chart)
    totals: dict = {}
    for key, code in mapping.items():
        a = accts.get(code)
        if not a or a["kind"] != "line" or "memo" in a.get("tags", []):
            continue
        v = values.get(key)
        if v is not None:
            totals[code] = totals.get(code, 0.0) + v
    return totals


def computed(chart, code: str, totals: dict, _memo=None) -> float:
    accts = _by_code(chart)
    a = accts[code]
    if a["kind"] == "line":
        return totals.get(code, 0.0)
    return (sum(computed(chart, c, totals) for c in a.get("sum", []))
            - sum(computed(chart, c, totals) for c in a.get("minus", [])))


def check_subtotals(chart, mapping: dict, values: dict, tol: float = 2.0) -> list[dict]:
    """One row per chart subtotal that the company printed (lowest line number wins when it prints it twice)."""
    accts = _by_code(chart)
    totals = account_totals(chart, mapping, values)
    printed: dict = {}
    for key in sorted(mapping, key=lambda k: (k[0], k[1])):
        code = mapping[key]
        if code in accts and accts[code]["kind"] == "subtotal" and code not in printed and values.get(key) is not None:
            printed[code] = values[key]
    rows = []
    for code, p in printed.items():
        c = computed(chart, code, totals)
        rows.append({"code": code, "name": accts[code]["name"], "printed": p, "computed": c, "difference": p - c,
                     "status": "pass" if abs(p - c) <= tol else "differs"})
    return rows
