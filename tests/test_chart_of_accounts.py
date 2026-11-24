"""The chart of accounts must be well formed and its subtotals must agree with each other."""
from fsa.map.chart import label_index, load_chart, validate_chart
from fsa.extract.pdf_statements import norm_label


def test_chart_is_well_formed():
    chart = load_chart()
    assert validate_chart(chart) == []


def test_every_statement_has_its_headline_totals():
    codes = {a["code"] for a in load_chart()["accounts"]}
    for c in ("PL_GROSS_PROFIT", "PL_OPERATING_PROFIT", "PL_PROFIT_BEFORE_TAX", "PL_PROFIT_FOR_YEAR",
              "BS_TOTAL_ASSETS", "BS_TOTAL_LIABILITIES", "BS_TOTAL_EQUITY", "BS_TOTAL_EQUITY_AND_LIABILITIES",
              "CF_OPERATING", "CF_INVESTING", "CF_FINANCING", "CF_CASH_CLOSING"):
        assert c in codes


def test_memo_accounts_never_enter_a_sum():
    chart = load_chart()
    memo = {a["code"] for a in chart["accounts"] if "memo" in a.get("tags", [])}
    assert memo, "the PPE build-up memo account should exist"
    for a in chart["accounts"]:
        assert not (set(a.get("sum", [])) & memo)


def test_a_damaged_label_still_matches():
    idx = label_index(load_chart())
    # 'Administrative' printed with a dropped 'ti' ligature and stray spaces still finds its account
    assert any(code == "PL_ADMIN_EXPENSES" for _, code in idx[norm_label("Administra ve  expenses")])
