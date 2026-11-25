"""Macro loader and USD view (a missing rate is never zero), and the notes scan (explicit restatement wording only)."""
import duckdb

from fsa.analyse import usd
from fsa.extract import notes_scan as ns
from fsa.macro import load as macro
from fsa.warehouse.db import SCHEMA


def memory_db():
    con = duckdb.connect(":memory:")
    con.execute(SCHEMA.read_text())
    return con


def test_loader_inverts_imf_rates_and_names_every_source():
    con = memory_db()
    got = macro.load(con)
    avg = macro.rate(con, "FX_USDNGN_OFFICIAL_AVG_ANNUAL", 2024)
    assert abs(avg["value"] - 1479.0) < 0.1 and avg["vintage_id"] == got["vintage_id"] and len(avg["source_sha256"]) == 64 and "IMF" in avg["source_name"]
    assert abs(macro.rate(con, "FX_USDNGN_OFFICIAL_CLOSE_YEAREND", 2023)["value"] - 899.9) < 0.1
    assert all(c["within_tolerance"] for c in macro.cross_check(con))


def test_missing_rate_is_none_not_zero_and_a_reload_is_a_new_vintage():
    con = memory_db()
    macro.load(con)
    assert macro.rate(con, "FX_USDNGN_OFFICIAL_AVG_ANNUAL", 2031) is None
    second = macro.load(con)
    assert con.execute("SELECT count(*) FROM macro_vintages").fetchone()[0] == 2 and second["vintage_id"].endswith("002")
    assert con.execute("SELECT count(DISTINCT vintage_id) FROM macro_series").fetchone()[0] == 2        # history is kept, never overwritten


def test_usd_view_without_macro_data_says_not_available():
    v = usd.usd_view(memory_db(), None, None, {"rows": []}, "T", 2025)
    assert v["status"] == "not available" and "load-macro" in v["reason"]
    assert "not available" in usd.to_markdown(v)[0]


def test_notes_scan_keeps_explicit_restatement_and_drops_routine_wording():
    assert ns._has("The comparative figures have been restated to correct an error.", ns.STRONG)
    assert ns._has("Profit before tax as previously reported was 100.", ns.STRONG)
    assert not ns._has("Items are stated at indexed cost less depreciation.", ns.STRONG)          # 'stated' is not 'restated'
    assert ns._has("Monetary items are not restated.", ns.ROUTINE)
    assert ns._has("Items that may be reclassified to profit or loss.", ns.ROUTINE)
    assert ns._has("The component's results have been restated to take account of inflation.", ns.INFLATION)
    assert ns._has("The prior-period error was corrected.", ns.STRONG) and ns._has("The have been resta\x00ed amounts", ["have been restated"]) is False
