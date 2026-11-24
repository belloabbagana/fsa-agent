from fsa.map.chart import load_chart
from fsa.map.rules_mapper import propose

CHART = load_chart()


def one(statement, section, label):
    return propose(CHART, [{"statement": statement, "seq": 1, "section": section, "label": label}])[0]


def test_unique_label_maps_exactly():
    p = one("profit_or_loss", "", "Production cost of sales")
    assert (p["code"], p["confidence"]) == ("PL_COST_OF_SALES", "exact")


def test_shared_label_is_decided_by_the_section_heading():
    assert one("financial_position", "Current liabilities", "Borrowings")["code"] == "BS_BORROWINGS_C"
    assert one("financial_position", "Non-current liabilities", "Borrowings")["code"] == "BS_BORROWINGS_NC"
    assert one("financial_position", "Non-current assets", "Lease receivables")["code"] == "BS_LEASE_RECEIVABLES_NC"


def test_damaged_label_still_maps():
    assert one("profit_or_loss", "", "Administra ve expenses")["code"] == "PL_ADMIN_EXPENSES"


def test_unknown_and_unlabelled_lines_get_no_code():
    assert one("profit_or_loss", "", "Something the chart has never seen")["code"] is None
    assert one("cash_flows", "Changes in:", "")["code"] is None


def test_ambiguous_without_a_section_is_not_guessed():
    p = one("financial_position", "Equity", "Borrowings")
    assert p["code"] is None and "ambiguous" in p["reason"]
