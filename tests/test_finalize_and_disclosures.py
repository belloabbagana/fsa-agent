"""Tests for settling line mappings and for the disclosure register (synthetic data only)."""
import duckdb

from fsa.map.chart import load_chart
from fsa.map.finalize import finalize
from fsa.report import disclosures as D
from fsa.warehouse.db import SCHEMA

CHART = load_chart()


def L(seq, label, value=None, statement="cash_flows"):
    return {"statement": statement, "seq": seq, "label": label, "value": value}


def test_authority_order():
    lines = [L(1, "Loans repaid", -5), L(2, "Odd line", -1), L(3, "Unclaimed dividend invested 32.4a", -2), L(4, "Silent line", -3)]
    rule = {("cash_flows", 1): "CF_LOANS_REPAID", ("cash_flows", 2): "CF_OTHER_NON_CASH", ("cash_flows", 3): "CF_DIVIDENDS_PAID", ("cash_flows", 4): "CF_INTEREST_PAID"}
    model = {("cash_flows", 1): "CF_LOANS_REPAID", ("cash_flows", 2): "CF_IMPAIRMENT_WRITEOFF", ("cash_flows", 3): "CF_DIVIDENDS_PAID", ("cash_flows", 4): None}
    out = {r["seq"]: r for r in finalize(CHART, lines, rule, model, [])}
    assert out[1]["basis"] == "agreed" and out[1]["status"] == "settled"
    assert out[2]["basis"] == "rule_disputed" and out[2]["status"] == "open"          # different codes, no decision
    assert out[4]["basis"] == "rule_model_silent" and out[4]["status"] == "settled"   # model abstained: the rule stands


def test_only_approved_decisions_settle_a_dispute():
    lines = [L(1, "Odd line", -1)]
    rule, model = {("cash_flows", 1): "CF_OTHER_NON_CASH"}, {("cash_flows", 1): "CF_IMPAIRMENT_WRITEOFF"}
    proposed = [{"statement": "cash_flows", "label": "Odd line", "code": "CF_IMPAIRMENT_WRITEOFF", "status": "proposed"}]
    assert finalize(CHART, lines, rule, model, proposed)[0]["status"] == "open"
    approved = [dict(proposed[0], status="approved")]
    r = finalize(CHART, lines, rule, model, approved)[0]
    assert (r["code"], r["basis"], r["status"]) == ("CF_IMPAIRMENT_WRITEOFF", "analyst_decision", "settled")


def test_unlabelled_total_is_accepted_only_if_the_sum_reproduces_it():
    base = [L(1, "Revenue", 100, "profit_or_loss"), L(2, "Cost of sales", -40, "profit_or_loss"), L(3, "", 60, "profit_or_loss")]
    rule = {("profit_or_loss", 1): "PL_REVENUE", ("profit_or_loss", 2): "PL_COST_OF_SALES", ("profit_or_loss", 3): None}
    good = finalize(CHART, base, rule, {**rule, ("profit_or_loss", 3): "PL_GROSS_PROFIT"}, [])
    assert good[2]["basis"] == "model_accepted"
    bad_lines = base[:2] + [L(3, "", 75, "profit_or_loss")]
    bad = finalize(CHART, bad_lines, rule, {**rule, ("profit_or_loss", 3): "PL_GROSS_PROFIT"}, [])
    assert bad[2]["status"] == "open" or bad[2]["basis"] == "intermediate_total"      # 75 is not 100-40: not accepted as gross profit
    assert bad[2]["basis"] != "model_accepted"


def _db():
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA.read_text())
    return con


def _line(con, fy, seq, statement, label, value, entity="group", end=None, unit="NGN_million", page=10):
    from datetime import date
    con.execute("INSERT INTO pdf_statement_lines VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ["T", fy, statement, seq, "", label, None, entity, end or date(fy, 12, 31), value, unit, "ok", page, "s"])


def test_disclosures_report_restatements_cash_definition_and_standard_notes(tmp_path):
    from datetime import date
    con = _db()
    con.execute("INSERT INTO source_files (source_id, sha256, file_name, provider, ticker, view) VALUES ('s','x','T_FY2025_annual_report.pdf','annual_report','T','pdf')")
    _line(con, 2025, 1, "financial_position", "Cash and cash equivalents", 1000.0)
    _line(con, 2025, 2, "financial_position", "Bank overdraft", 100.0)
    _line(con, 2025, 3, "cash_flows", "Cash and cash equivalents at end of year", 900.0)
    for seq, lab, code, val in [(1, "Cash and cash equivalents", "BS_CASH", 1000.0), (2, "Bank overdraft", "BS_BANK_OVERDRAFT", 100.0)]:
        con.execute("INSERT INTO mapping_final VALUES ('T',2025,'financial_position',?,?,?,'agreed','settled',?,?,'')", [seq, lab, code, code, code])
    con.execute("INSERT INTO mapping_final VALUES ('T',2025,'cash_flows',3,'Cash and cash equivalents at end of year','CF_CASH_CLOSING','agreed','settled','CF_CASH_CLOSING','CF_CASH_CLOSING','')")
    con.execute("INSERT INTO integrity_checks (ticker,fiscal_year,statement,entity,period_end,check_name,expected,actual,status) VALUES ('T',2025,'cash_flows','group',?,?,821080,821184,'fail')",
                [date(2024, 12, 31), "comparative:Net cash from operating activities"])
    items = D.generate(con, CHART, "T", 2025, notes_path=tmp_path / "none.yaml")
    cats = {i["category"] for i in items}
    assert {"standard", "restatement", "cash_definition", "mapping"} <= cats
    cash = next(i for i in items if i["category"] == "cash_definition")
    assert "nets overdrafts against cash" in cash["text"]                    # the difference equals the overdraft: explained
    rest = next(i for i in items if i["category"] == "restatement")
    assert "821,080" in rest["text"] and "821,184" in rest["text"] and "not state the reason" in rest["text"]
    assert any("not investment advice" in i["text"] for i in items)
    assert "Important" in D.to_markdown("T", 2025, items)


def test_unlabelled_row_without_a_figure_is_not_an_open_decision():
    lines = [L(1, "", None, "profit_or_loss"), L(2, "", 0.0, "profit_or_loss")]
    rule = {("profit_or_loss", 1): None, ("profit_or_loss", 2): None}
    model = {("profit_or_loss", 1): "OCI_NET", ("profit_or_loss", 2): "OCI_NET"}
    out = finalize(CHART, lines, rule, model, [])
    assert all(r["status"] == "settled" and r["basis"] == "intermediate_total" for r in out)
