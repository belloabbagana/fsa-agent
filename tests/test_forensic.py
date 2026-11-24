"""Forensic scores against known anchors, and the guard that a missing input never becomes a number."""
import pytest

from fsa.analyse.forensic import band_for, evaluate, load_rules
from fsa.analyse.ratios import load_rules as load_ratio_rules
from fsa.map.chart import load_chart

CHART, RR, FR = load_chart(), load_ratio_rules(), load_rules()
BASE = {"PL_REVENUE": 100.0, "PL_GROSS_PROFIT": 40.0, "PL_COST_OF_SALES": -60.0, "PL_ADMIN_EXPENSES": -10.0, "PL_SELLING_DISTRIBUTION": -5.0,
        "BS_TRADE_RECEIVABLES": 10.0, "BS_TOTAL_CURRENT_ASSETS": 50.0, "BS_PPE": 30.0, "BS_TOTAL_ASSETS": 100.0, "CF_DEPRECIATION_AMORTISATION": 5.0,
        "BS_TOTAL_CURRENT_LIABILITIES": 20.0, "BS_BORROWINGS_NC": 10.0, "BS_LEASE_LIABILITIES_NC": 0.0, "PL_PROFIT_FOR_YEAR": 10.0, "CF_OPERATING": 10.0,
        "BS_INVENTORIES": 8.0, "BS_TRADE_PAYABLES": 12.0, "PL_OPERATING_PROFIT": 15.0, "BS_RETAINED_EARNINGS": 20.0, "BS_TOTAL_EQUITY": 60.0, "BS_TOTAL_LIABILITIES": 40.0}


def get(res, i):
    return next(r for r in res if r["ratio_id"] == i)


def test_a_neutral_company_scores_minus_2_48_on_beneish():
    res = evaluate(CHART, RR, FR, dict(BASE), dict(BASE))             # nothing changed, profit equals operating cash flow
    for idx in ("dsri", "gmi", "aqi", "sgi", "depi", "sgai", "lvgi"):
        assert get(res, idx)["value"] == pytest.approx(1.0)
    assert get(res, "tata")["value"] == pytest.approx(0.0)
    assert get(res, "beneish_m")["value"] == pytest.approx(-2.48, abs=0.005)
    assert "less like" in get(res, "beneish_m")["band"]                 # -2.48 is below -2.22


def test_altman_emerging_market_arithmetic():
    z = get(evaluate(CHART, RR, FR, dict(BASE), dict(BASE)), "altman_z")
    expected = 3.25 + 6.56 * 0.30 + 3.26 * 0.20 + 6.72 * 0.15 + 1.05 * 1.5
    assert z["value"] == pytest.approx(expected) and z["band"] == "safe zone"


def test_bands_use_the_published_cut_offs():
    item = next(i for i in FR["ratios"] if i["id"] == "beneish_m")
    assert "above the -1.78" in band_for(item, -1.0) and "grey" in band_for(item, -2.0) and "less like" in band_for(item, -3.0)
    z = next(i for i in FR["ratios"] if i["id"] == "altman_z")
    assert (band_for(z, 6.0), band_for(z, 5.0), band_for(z, 4.0)) == ("safe zone", "grey zone", "distress zone")


def test_receivables_outgrowing_revenue_trips_the_flag():
    cur = dict(BASE, BS_TRADE_RECEIVABLES=14.0)                       # +40% against revenue +0%
    r = get(evaluate(CHART, RR, FR, cur, dict(BASE)), "receivables_growth_gap")
    assert r["value"] == pytest.approx(40.0) and r["flagged"] is True
    assert get(evaluate(CHART, RR, FR, dict(BASE), dict(BASE)), "receivables_growth_gap")["flagged"] is False


def test_no_opening_year_means_not_meaningful_not_a_score():
    res = evaluate(CHART, RR, FR, dict(BASE), None)
    assert get(res, "beneish_m")["status"] == "N/M" and get(res, "altman_z")["status"] == "ok"   # Altman needs no opening year


def test_documented_coefficients_match_the_formula():
    """The coefficients shown as 'what drives the score' must agree with the formula that actually computes it."""
    from fsa.analyse.forensic import contributions
    res = evaluate(CHART, RR, FR, dict(BASE, BS_TRADE_RECEIVABLES=14.0, CF_OPERATING=6.0), dict(BASE))
    for model in ("beneish_m", "altman_z"):
        item = next(i for i in FR["ratios"] if i["id"] == model)
        rebuilt = item["coefficients"]["constant"] + sum(x for _, _, x in contributions(item, res))
        assert rebuilt == pytest.approx(get(res, model)["value"])
