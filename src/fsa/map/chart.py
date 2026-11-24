"""Load and check the chart of accounts (rules/chart_of_accounts/corporate.yaml).

Checks the file is well formed (unique codes, every subtotal refers to real accounts) and measures how
many of the labels printed in the extracted reports the chart already recognises. Recognising a label
here is only a first filter: the real mapping (week 5) is proposed by a model and approved by the analyst.
"""
from collections import defaultdict
from pathlib import Path

import yaml

from fsa.extract.pdf_statements import norm_label
from fsa.warehouse.db import ROOT

CHART_PATH = ROOT / "rules" / "chart_of_accounts" / "corporate.yaml"
STATEMENT_GROUPS = {   # which chart statements a printed statement can map to
    "profit_or_loss": {"profit_or_loss", "other_comprehensive_income"},
    "comprehensive_income": {"profit_or_loss", "other_comprehensive_income"},
    "financial_position": {"financial_position"},
    "cash_flows": {"cash_flows"},
}


def load_chart(path: Path = CHART_PATH) -> dict:
    return yaml.safe_load(path.read_text())


def validate_chart(chart: dict) -> list[str]:
    """Return a list of problems (empty = well formed)."""
    problems, codes = [], set()
    accounts = chart["accounts"]
    for a in accounts:
        c = a.get("code")
        if c in codes:
            problems.append(f"duplicate code {c}")
        codes.add(c)
        if a.get("kind") not in {"line", "subtotal"}:
            problems.append(f"{c}: kind must be 'line' or 'subtotal'")
        if a.get("statement") not in chart["statements"]:
            problems.append(f"{c}: unknown statement {a.get('statement')!r}")
        if a.get("kind") == "line" and not (a.get("what") and a.get("why")):
            problems.append(f"{c}: a line needs 'what' and 'why' (the CFA explanation)")
        if a.get("kind") == "line" and not a.get("labels"):
            problems.append(f"{c}: a line needs at least one printed label example")
    for a in accounts:
        if a.get("kind") == "subtotal":
            if not a.get("sum"):
                problems.append(f"{a['code']}: a subtotal needs 'sum'")
            for part in a.get("sum", []) + a.get("minus", []):
                if part not in codes:
                    problems.append(f"{a['code']}: sum refers to unknown account {part}")
                elif next(x for x in accounts if x["code"] == part).get("tags") and "memo" in next(x for x in accounts if x["code"] == part)["tags"]:
                    problems.append(f"{a['code']}: a memo account must not be in a sum ({part})")
    return problems


def label_index(chart: dict) -> dict:
    """normalised label -> set of (statement, code)"""
    idx = defaultdict(set)
    for a in chart["accounts"]:
        for lab in a.get("labels", []):
            idx[norm_label(lab)].add((a["statement"], a["code"]))
    return idx


def coverage(con, chart: dict, ticker: str | None = None):
    """For each distinct printed label in the group statements: is it recognised by the chart?"""
    idx = label_index(chart)
    q = ("SELECT statement, label, string_agg(DISTINCT ticker || CAST(fiscal_year AS VARCHAR), ' ') "
         "FROM pdf_statement_lines WHERE entity='group' AND label<>'' GROUP BY statement, label")
    seen, unknown = {}, []
    for statement, label, where in con.execute(q).fetchall():
        key = (statement, norm_label(label))
        if key in seen:
            continue
        allowed = STATEMENT_GROUPS.get(statement, set())
        hits = {code for st, code in idx.get(key[1], set()) if st in allowed}
        seen[key] = bool(hits)
        if not hits:
            unknown.append((statement, label.replace("\x00", "·"), where))
    return seen, unknown
