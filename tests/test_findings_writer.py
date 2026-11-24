"""The writer's code checks: numbers must come from the supplied figures and accusation or advice wording is rejected."""
from fsa.findings.writer import check_text, parse, show

REC = {"fiscal_year": 2025, "title": "Operating cash flow well below profit", "trigger": "cfo_to_net_income < 0.8", "question": "Which items?",
       "facts": [{"name": "cfo_to_net_income", "value": 0.5769}, {"name": "CF_OPERATING", "value": 70019.87}, {"name": "CF_WC_PAYABLES", "value": -73833.84}]}


def test_figures_from_the_facts_pass():
    assert check_text(REC, "Operating cash flow was 70,020 against 0.58 times profit in 2025; payables used 73,834.") == []


def test_a_number_the_model_calculated_is_rejected():
    assert any("43" in p for p in check_text(REC, "Cash conversion was 43% lower than profit."))


def test_accusation_and_advice_words_are_rejected():
    assert check_text(REC, "This suggests manipulation.") and check_text(REC, "Investors should buy.")


def test_parse_needs_both_fields():
    assert parse('x {"observation": "a", "why_it_matters": "b"} y') == {"observation": "a", "why_it_matters": "b"}
    assert parse('{"observation": "a"}') is None
    assert show(70019.87) == "70,020" and show(0.5769) == "0.58" and show(12.34) == "12.3"


def test_a_currency_letter_before_a_figure_does_not_split_it():
    assert check_text(REC, "Operating cash flow was N70,020 million and payables used N73,834 million.") == []


def test_selling_prices_are_fine_but_a_sell_call_is_not():
    assert check_text(REC, "Selling and distribution costs rose.") == []
    assert check_text(REC, "Investors should sell.")
