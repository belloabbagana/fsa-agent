"""Finding rules: they load and parse, only known names are used, and a missing input is never read as 'no finding'."""
import ast
import re

import pytest

from fsa.analyse.ratios import Evaluator, NotMeaningful, load_rules as load_ratio_rules
from fsa.findings.triggers import SUFFIX, load_rules, rate
from fsa.map.chart import load_chart

CHART, RR = load_chart(), load_ratio_rules()
CODES = {a["code"] for a in CHART["accounts"]} | set(RR["metrics"])
EXTRA = {"restated_lines", "later_restated_lines", "flags_tripped", "has_acquisition"}
RULES = load_rules()


def names(expr: str) -> set:
    return {n.id for n in ast.walk(ast.parse(expr, mode="eval")) if isinstance(n, ast.Name)} - {"prior", "avg", "abs", "min", "max"}


def test_rule_ids_are_unique_and_complete():
    ids = [r["id"] for r in RULES]
    assert len(ids) == len(set(ids))
    for r in RULES:
        for field in ("section", "title", "type", "question", "why", "hypotheses", "who_answers"):
            assert r.get(field), f"{r['id']} lacks {field}"
        for h in r["hypotheses"]:
            assert h["h"] and h["confirm"] and h["rule_out"], r["id"]


def test_every_name_in_a_numeric_rule_is_known():
    ratio_ids = {x["id"] for x in RR["ratios"]} | {x["id"] for x in __import__("yaml").safe_load(open("rules/forensic.yaml"))["ratios"]}
    for r in RULES:
        if r["kind"] != "numeric":
            continue
        exprs = [r["trigger"]] + [v for k, v in r["materiality"].items() if k in ("measure", "escalate_if")]
        for e in exprs:
            for n in names(e):
                base = SUFFIX.match(n)["base"] if n.islower() and SUFFIX.match(n) else n
                assert base in CODES or base in ratio_ids or n in EXTRA, f"{r['id']}: unknown name {n}"


def test_missing_peer_input_makes_a_rule_not_tested():
    def lookup(base, kind):
        raise NotMeaningful("no peer figure")
    from fsa.findings.triggers import TriggerEvaluator
    ev = TriggerEvaluator({"PL_REVENUE": 1.0}, None, RR["metrics"], {}, CODES, {}, lookup)
    with pytest.raises(NotMeaningful):
        ev.ev(ast.parse("x_peer_median > 1", mode="eval").body)


def test_and_stops_at_first_deciding_part_and_abs_min_max_work():
    ev = Evaluator({"PL_REVENUE": -5.0}, None, {}, {}, CODES)
    assert ev.ev(ast.parse("PL_REVENUE > 0 and prior(PL_REVENUE) > 0", mode="eval").body) is False   # no opening balance needed
    assert ev.ev(ast.parse("abs(PL_REVENUE)", mode="eval").body) == 5.0
    assert ev.ev(ast.parse("max(PL_REVENUE, 2)", mode="eval").body) == 2


def test_materiality_levels_and_escalation():
    ev = Evaluator({"PL_REVENUE": 12.0, "PL_PROFIT_BEFORE_TAX": 100.0}, None, {}, {}, CODES)
    spec = {"measure": "PL_REVENUE", "high": ">= 10", "medium": ">= 3"}
    assert rate(spec, ev)[0] == "high"
    assert rate({"measure": "PL_REVENUE / 10", "high": ">= 10", "medium": ">= 1"}, ev)[0] == "medium"
    assert rate({"fixed": "low", "escalate_if": "PL_REVENUE > 5"}, ev)[0] == "high"
    assert rate({"fixed": "low", "escalate_if": "prior(PL_REVENUE) > 5"}, ev)[0] == "low"        # unavailable escalation never raises


def test_record_markdown_carries_disclaimer_and_sources():
    from fsa.findings.records import to_markdown
    rec = {"id": "X-1", "title": "t", "materiality": "high", "type": "risk", "trigger": "a > 1", "materiality_note": "", "question": "q", "why": "w",
           "hypotheses": [{"h": "h", "confirm": "c", "rule_out": "r"}], "who_answers": "management", "docs_needed": ["note"],
           "facts": [{"name": "PL_REVENUE", "value": 1234.5, "source": [{"statement": "profit_or_loss", "label": "Revenue", "page": 12}]}, {"name": "dso", "value": None, "note": "not available: x"}]}
    md = to_markdown([rec], "T", 2025)
    assert "not investment advice" in md and "profit_or_loss p.12 'Revenue'" in md and "n/a" in md
