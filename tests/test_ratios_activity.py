"""New activity and performance ratios (rules v0.2): turnover is the inverse of days; DuPont legs multiply back to ROE."""
from fsa.analyse.ratios import load_rules, run_ratios
from fsa.map.chart import load_chart

CHART, RULES = load_chart(), load_rules()
CODES = [a["code"] for a in CHART["accounts"]]


def ratios(cur_over: dict, prior_over: dict) -> dict:
    cur = {c: 0.0 for c in CODES} | cur_over
    prior = {c: 0.0 for c in CODES} | prior_over
    return {r["ratio_id"]: r for r in run_ratios(RULES, CHART, cur, prior)}


CUR = {"PL_REVENUE": 1000.0, "PL_COST_OF_SALES": -600.0, "PL_OPERATING_PROFIT": 300.0, "PL_PROFIT_BEFORE_TAX": 250.0, "PL_PROFIT_FOR_YEAR": 175.0,
       "PL_ATTRIB_OWNERS": 175.0, "BS_TRADE_RECEIVABLES": 100.0, "BS_INVENTORIES": 150.0, "BS_TRADE_PAYABLES": 120.0, "BS_TOTAL_ASSETS": 1200.0,
       "BS_EQUITY_OWNERS": 600.0, "BS_TOTAL_EQUITY": 600.0, "BS_TOTAL_CURRENT_ASSETS": 400.0, "BS_TOTAL_CURRENT_LIABILITIES": 250.0}
PRIOR = {k: v for k, v in CUR.items() if k.startswith("BS_")}


def test_turnover_is_inverse_of_days():
    r = ratios(CUR, PRIOR)
    for turn, days in (("receivables_turnover", "dso"), ("inventory_turnover", "dio"), ("payables_turnover", "dpo")):
        assert abs(r[turn]["value"] * r[days]["value"] - 365) < 1e-6, turn


def test_dupont_legs_multiply_to_roe():
    r = ratios(CUR, PRIOR)
    pm = r["operating_margin"]["value"] / 100
    roe = pm * r["interest_burden"]["value"] * r["tax_burden"]["value"] * r["asset_turnover"]["value"] * r["equity_multiplier"]["value"] * 100
    assert abs(roe - r["roe"]["value"]) < 1e-6


def test_negative_working_capital_is_not_meaningful_not_zero():
    r = ratios(CUR | {"BS_TOTAL_CURRENT_ASSETS": 100.0}, PRIOR | {"BS_TOTAL_CURRENT_ASSETS": 100.0})
    assert r["working_capital_turnover"]["status"] != "ok" and r["working_capital_turnover"]["value"] is None
