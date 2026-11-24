"""The draft rating rules parse, every expression is valid, and every name is one the engine already knows or an input still to be built."""
import ast

import yaml

from fsa.analyse.ratios import load_rules as load_ratio_rules
from fsa.findings.triggers import SUFFIX
from fsa.map.chart import load_chart
from fsa.warehouse.db import ROOT

R = yaml.safe_load((ROOT / "rules" / "ratings.yaml").read_text())
RR = load_ratio_rules()
CODES = {a["code"] for a in load_chart()["accounts"]} | set(RR["metrics"])
RATIO_IDS = {x["id"] for x in RR["ratios"]} | {x["id"] for x in yaml.safe_load((ROOT / "rules" / "forensic.yaml").read_text())["ratios"]}
ENGINE_EXTRAS = {"restated_lines", "later_restated_lines", "flags_tripped"}
ENGINE_EXTRAS |= {"auditor_adverse", "open_mapping_lines", "unsigned_mapping", "filing_arithmetic_notes", "pdf_quality_notes", "provider_differences",
                   "peer_set_unchecked", "health_distressed", "health_weak", "health_ok", "n_improving", "n_worsening", "n_critical", "n_weak", "n_strong", "n_rated", "points"}
STILL_TO_BUILD = set()
FUNCS = {"prior", "avg", "abs", "min", "max", "True"}


def expressions():
    for name, p in R["financial_health"]["pillars"].items():
        for lv in p["levels"]:
            yield f"pillar {name}", lv["when"]
    for o in R["financial_health"]["overall"]:
        yield "health overall", o["when"]
    for t in R["earnings_quality"]["tests"]:
        yield f"eq {t['id']}", t["when"]
    for lv in R["earnings_quality"]["levels"]:
        yield "eq level", lv["when"]
    for lv in R["forensic_risk"]["levels"] + R["data_quality"]["levels"] + R["conclusion"]["levels"]:
        yield "levels", lv["when"]


def test_every_expression_parses_and_uses_known_names():
    for where, e in expressions():
        for n in {x.id for x in ast.walk(ast.parse(e, mode="eval")) if isinstance(x, ast.Name)} - FUNCS:
            base = SUFFIX.match(n)["base"] if SUFFIX.match(n) else n
            assert n in CODES | RATIO_IDS | ENGINE_EXTRAS | STILL_TO_BUILD or base in CODES | RATIO_IDS, f"{where}: unknown name {n} in {e!r}"


def test_levels_end_with_a_catch_all_and_scales_are_complete():
    assert [o["rating"] for o in R["financial_health"]["overall"]] == ["Distressed", "Weak", "Strong", "Healthy", "Mixed"]
    assert [lv["rating"] for lv in R["earnings_quality"]["levels"]] == ["Questionable", "Low", "Moderate", "Good", "High"]
    assert [lv["rating"] for lv in R["forensic_risk"]["levels"]] == ["Red", "Orange", "Yellow", "Green"]
    assert [lv["grade"] for lv in R["data_quality"]["levels"]] == ["Low", "Moderate", "High"]
    for p in R["financial_health"]["pillars"].values():
        assert p["levels"][-1]["when"] == "True" and p["why"]


def test_red_needs_auditor_tests():
    red = R["forensic_risk"]["levels"][0]
    assert red["rating"] == "Red" and "auditor_adverse" in red["needs"] and R["forensic_risk"]["red_requires_auditor_tests"]
