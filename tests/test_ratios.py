"""The ratio evaluator: correct arithmetic, honest N/M, and no way to run arbitrary code from a rule file."""
import ast

import pytest

from fsa.analyse.ratios import Evaluator, NotMeaningful

CODES = {"PL_REVENUE", "PL_GROSS_PROFIT", "BS_TOTAL_ASSETS", "BS_TRADE_RECEIVABLES", "PL_PROFIT_FOR_YEAR"}


def run(expr, cur, prior=None, metrics=None, ratios=None):
    ev = Evaluator(cur, prior, metrics or {}, ratios or {}, CODES)
    return ev.ev(ast.parse(expr, mode="eval").body)


def test_margin_and_average_balance_days():
    cur = {"PL_REVENUE": 200.0, "PL_GROSS_PROFIT": 50.0, "BS_TRADE_RECEIVABLES": 30.0}
    prior = {"BS_TRADE_RECEIVABLES": 10.0}
    assert run("PL_GROSS_PROFIT / PL_REVENUE * 100", cur) == 25.0
    assert run("avg(BS_TRADE_RECEIVABLES) / PL_REVENUE * 365", cur, prior) == pytest.approx(20 / 200 * 365)


def test_missing_opening_balance_is_not_meaningful_not_zero():
    with pytest.raises(NotMeaningful):
        run("avg(BS_TRADE_RECEIVABLES)", {"BS_TRADE_RECEIVABLES": 5.0}, None)


def test_zero_denominator_is_not_meaningful():
    with pytest.raises(NotMeaningful):
        run("PL_GROSS_PROFIT / PL_REVENUE", {"PL_GROSS_PROFIT": 5.0, "PL_REVENUE": 0.0})


def test_conditions_and_metrics():
    metrics = {"DOUBLE_REV": {"formula": "PL_REVENUE * 2"}, "BAD": {"formula": "PL_REVENUE", "nm_if": ["PL_REVENUE <= 0"]}}
    assert run("DOUBLE_REV + 1", {"PL_REVENUE": 3.0}, metrics=metrics) == 7.0
    assert run("PL_REVENUE <= 0 or PL_GROSS_PROFIT > 10", {"PL_REVENUE": 1.0, "PL_GROSS_PROFIT": 11.0}) is True
    with pytest.raises(NotMeaningful):
        run("BAD", {"PL_REVENUE": -1.0}, metrics=metrics)


def test_a_rule_that_depends_on_a_not_meaningful_ratio_is_not_meaningful():
    ratios = {"dso": {"status": "N/M", "reason": "revenue is not above 0", "value": None}}
    with pytest.raises(NotMeaningful):
        run("dso + 1", {}, ratios=ratios)


@pytest.mark.parametrize("bad", ["__import__('os').system('echo hi')", "PL_REVENUE ** 2", "open('x')", "[1,2]", "PL_REVENUE.real"])
def test_rules_cannot_run_arbitrary_code(bad):
    with pytest.raises((ValueError, SyntaxError)):
        run(bad, {"PL_REVENUE": 1.0})


def test_unknown_names_are_rejected():
    with pytest.raises(ValueError):
        run("NOT_A_CODE + 1", {})


def test_a_metric_can_be_checked_against_its_own_value():
    metrics = {"RATE": {"formula": "PL_PROFIT_FOR_YEAR / PL_REVENUE", "nm_if": ["PL_REVENUE <= 0", "RATE > 0.6"]}}
    assert run("RATE", {"PL_PROFIT_FOR_YEAR": 3.0, "PL_REVENUE": 10.0}, metrics=metrics) == pytest.approx(0.3)
    with pytest.raises(NotMeaningful):
        run("RATE", {"PL_PROFIT_FOR_YEAR": 9.0, "PL_REVENUE": 10.0}, metrics=metrics)
