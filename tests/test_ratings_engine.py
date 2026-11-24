"""Rating engine: worst-to-best first match, and a test that cannot run is never read as passed or failed."""
from fsa.analyse.ratios import Evaluator, load_rules as load_ratio_rules
from fsa.findings.triggers import TriggerEvaluator
from fsa.map.chart import load_chart
from fsa.report import ratings as rtg

RR, CHART = load_ratio_rules(), load_chart()
KNOWN = {a["code"] for a in CHART["accounts"]}


def build_tester(now: dict, extra: dict | None = None) -> rtg.Tester:
    def lookup(base, kind):
        raise rtg.NotMeaningful("no history")
    rtg.Tester.metrics = RR["metrics"]
    return rtg.Tester(({}, None, now, KNOWN, {}, lookup), extra or {})


def ok(v):
    return {"status": "ok", "value": v, "reason": ""}


LEVELS = [{"level": "weak", "when": "roic < 10"}, {"level": "strong", "when": "roic >= 25"}, {"level": "adequate", "when": "True"}]


def test_first_matching_level_wins_worst_first():
    assert rtg.first_level(build_tester({"roic": ok(5.0)}), LEVELS, "level")[0] == "weak"
    assert rtg.first_level(build_tester({"roic": ok(30.0)}), LEVELS, "level")[0] == "strong"
    assert rtg.first_level(build_tester({"roic": ok(15.0)}), LEVELS, "level")[0] == "adequate"


def test_missing_input_is_not_tested_and_not_counted_as_run():
    label, untested, ran = rtg.first_level(build_tester({}), LEVELS, "level")
    assert label == "adequate" and ran == 0 and len(untested) == 2          # caller sees nothing was actually tested


def test_not_meaningful_ratio_is_not_tested():
    now = {"roic": {"status": "N/M", "value": None, "reason": "negative capital"}}
    assert rtg.first_level(build_tester(now), LEVELS, "level")[2] == 0


def test_equality_and_supplied_names_work():
    t = build_tester({}, {"health_ok": 1.0, "n_worsening": 0.0})
    assert t.test("health_ok >= 1 and n_worsening == 0")[0] is True
