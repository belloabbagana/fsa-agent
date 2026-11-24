"""Tests for the model safeguards and the arithmetic check. No network: the model call is faked."""
import pytest

from fsa.map.chart import load_chart
from fsa.map.model_proposer import ModelAnswer, WrongModelError, parse_proposals, verify_model
from fsa.map.validate_mapping import check_subtotals

CHART = load_chart()
CODES = {a["code"] for a in CHART["accounts"]}


def test_the_model_that_answered_must_match_the_request():
    ok = ModelAnswer("x", models_seen={"claude-haiku-5-5"}, models_billed={"claude-haiku-5-5"})
    assert verify_model(ok, "haiku") == {"claude-haiku-5-5"}
    with pytest.raises(WrongModelError):
        verify_model(ModelAnswer("x", models_seen={"claude-sonnet-5-5"}), "haiku")
    with pytest.raises(WrongModelError):           # mixed: Haiku plus a bigger model
        verify_model(ModelAnswer("x", models_seen={"claude-haiku-5-5"}, models_billed={"claude-opus-5-5"}), "haiku")
    with pytest.raises(WrongModelError):           # nothing reported: cannot confirm
        verify_model(ModelAnswer("x"), "haiku")


def test_invented_codes_are_rejected_and_none_is_allowed():
    text = 'Here: [{"id":"a","code":"PL_REVENUE","confidence":"high","reason":"r"},' \
           '{"id":"b","code":"MADE_UP","confidence":"high","reason":"r"},{"id":"c","code":"NONE","confidence":"low","reason":"r"}]'
    out, notes = parse_proposals(text, CODES, {"a", "b", "c"})
    by = {o["id"]: o for o in out}
    assert by["a"]["code"] == "PL_REVENUE" and by["b"]["code"] is None and by["b"]["confidence"] == "rejected"
    assert by["c"]["code"] is None and any("invented" in n for n in notes)


def test_garbled_answer_is_not_accepted():
    assert parse_proposals("sorry, I cannot", CODES, {"a"})[0] == []


def test_mapped_lines_must_add_up_to_the_printed_subtotal():
    mapping = {("pl", 1): "PL_REVENUE", ("pl", 2): "PL_COST_OF_SALES", ("pl", 3): "PL_GROSS_PROFIT"}
    good = check_subtotals(CHART, mapping, {("pl", 1): 100.0, ("pl", 2): -40.0, ("pl", 3): 60.0})
    assert good[0]["code"] == "PL_GROSS_PROFIT" and good[0]["status"] == "pass"
    bad = check_subtotals(CHART, mapping, {("pl", 1): 100.0, ("pl", 2): -40.0, ("pl", 3): 70.0})
    assert bad[0]["status"] == "differs" and bad[0]["difference"] == 10.0
    wrong = {**mapping, ("pl", 2): "PL_ADMIN_EXPENSES"}        # cost of sales mapped to the wrong account...
    assert check_subtotals(CHART, wrong, {("pl", 1): 100.0, ("pl", 2): -40.0, ("pl", 3): 60.0})[0]["status"] == "differs"  # ...breaks gross profit
