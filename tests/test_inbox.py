"""Tests for inbox detection, renaming and safe handling of odd files (made-up data only)."""
from datetime import date

import duckdb
import pytest

from fsa.ingest.inbox import detect, new_name, process_inbox
from fsa.warehouse.db import SCHEMA

ANNUAL = "## Income Statement\nMetric,FY 2025,FY 2024,FY 2022\nRevenue,3,2,1\n"
QUARTERLY = "## Income Statement\nMetric,Q2 2025,Q1 2025,Q4 2024\nRevenue,3,2,1\n"


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_detects_annual_and_quarterly_and_names_them(tmp_path):
    a = detect(write(tmp_path, "ABC-financials-2026-11-23.csv", ANNUAL))
    assert (a.ticker, a.view, a.timeline, a.downloaded) == ("ABC", "annual", "2022-2025", date(2026, 11, 23))
    q = detect(write(tmp_path, "ABC-financials-2026-11-23 (1).csv", QUARTERLY))
    assert (q.view, q.timeline) == ("quarterly", "2024Q4-2025Q2")
    assert new_name(a, "Abc Cement") == "ABC_Abc-Cement_annual_2022-2025_dl2026-11-23.csv"
    assert new_name(a, "ABC") == "ABC_annual_2022-2025_dl2026-11-23.csv"


def test_unrecognised_file_is_left_alone_and_reported(tmp_path):
    inbox, raw = tmp_path / "in", tmp_path / "raw"
    inbox.mkdir()
    write(inbox, "random.csv", "a,b\n1,2\n")
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA.read_text())
    report = process_inbox(con, inbox, raw, {})
    assert report[0].startswith("SKIPPED")
    assert (inbox / "random.csv").exists()


def test_new_company_is_loaded_as_unchecked_peer_and_newer_download_supersedes(tmp_path):
    inbox, raw = tmp_path / "in", tmp_path / "raw"
    inbox.mkdir()
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA.read_text())
    write(inbox, "ABC-financials-2026-11-23.csv", ANNUAL)
    process_inbox(con, inbox, raw, {})
    assert con.execute("SELECT role, conflict_check FROM companies WHERE ticker='ABC'").fetchone() == ("peer", "unchecked")
    write(inbox, "ABC-financials-2026-12-01.csv", ANNUAL.replace("Revenue,3,2,1", "Revenue,3,2,9"))
    process_inbox(con, inbox, raw, {})
    assert con.execute("SELECT count(*) FROM source_files WHERE is_current").fetchone()[0] == 1
    # the current view shows only the newer download; the old values are kept in the base table
    assert con.execute("SELECT value FROM statement_lines_current WHERE fiscal_year=2022").fetchone()[0] == 9
    assert con.execute("SELECT count(*) FROM statement_lines WHERE fiscal_year=2022").fetchone()[0] == 2
