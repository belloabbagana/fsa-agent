"""Known-answer tests for the NGN Market loader, using a tiny made-up export (no real data)."""
from datetime import date
from pathlib import Path

import duckdb

from fsa.ingest.ngnmarket import load_export, parse_period
from fsa.warehouse.db import SCHEMA

SAMPLE = """## Income Statement
Metric,Q2 2024,Q1 2024
Revenue,300,200
Operating Expenses,,50
EPS (Basic),1.5,1.0

## Ratios
Metric,Q2 2024,Q1 2024
Gross Margin (%),55.5,50.0
Current Ratio,0.8,0.9

## Dividends
Payment Date,Ex-Dividend Date,Amount (₦),Type,Yield (%)
2024-05-30,2024-04-15,30,12M,4.37
"""


def make(tmp_path: Path):
    f = tmp_path / "TEST_quarterly.csv"
    f.write_text(SAMPLE, encoding="utf-8")
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA.read_text())
    return con, f


def test_parse_period():
    assert parse_period("FY 2024") == ("FY", 2024, None, date(2024, 12, 31))
    assert parse_period("Q2 2024") == ("Q", 2024, 2, date(2024, 6, 30))


def test_values_units_and_missing_never_zero(tmp_path):
    con, f = make(tmp_path)
    load_export(con, f, "TEST", "quarterly", date(2026, 1, 1))
    rev = con.execute("SELECT value FROM statement_lines WHERE metric='Revenue' AND fiscal_quarter=2").fetchone()[0]
    assert rev == 300
    opex = con.execute("SELECT value, quality_flag FROM statement_lines WHERE metric='Operating Expenses' AND fiscal_quarter=2").fetchone()
    assert opex == (None, "missing")          # blank stays NULL, not 0
    assert con.execute("SELECT unit FROM statement_lines WHERE metric='EPS (Basic)' LIMIT 1").fetchone()[0] == "NGN_per_share"
    assert con.execute("SELECT unit FROM provider_ratios WHERE metric='Gross Margin (%)' LIMIT 1").fetchone()[0] == "pct"
    assert con.execute("SELECT unit FROM provider_ratios WHERE metric='Current Ratio' LIMIT 1").fetchone()[0] == "x"


def test_reload_does_not_duplicate(tmp_path):
    con, f = make(tmp_path)
    load_export(con, f, "TEST", "quarterly", date(2026, 1, 1))
    load_export(con, f, "TEST", "quarterly", date(2026, 1, 1))
    assert con.execute("SELECT count(*) FROM statement_lines").fetchone()[0] == 6
    assert con.execute("SELECT count(*) FROM dividends").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM source_files").fetchone()[0] == 1
