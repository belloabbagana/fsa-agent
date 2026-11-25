"""Load macro series into the warehouse from raw files in data/raw/macro/ (contract: docs/macro_interface.md).

Every row carries its source, retrieval time and the SHA-256 of the stored raw file. A new load is a NEW vintage; earlier vintages are never overwritten.
The official rate is the default (agreed decision 1). The IMF series are USD per NGN; this loader inverts them to NGN per USD by code and says so in the note.
The World Bank average is loaded as a separate cross-check series, never mixed into the official one.
"""
import hashlib
import json
from datetime import date, datetime, timezone

from fsa.warehouse.db import ROOT

RAW = ROOT / "data" / "raw" / "macro"
IMF_FILE, WB_FILE = RAW / "imf_ER_NGA_usd_xdc_2021_2025.json", RAW / "worldbank_PA.NUS.FCRF_NGA_2018_2025.json"
CATALOG = [
    ("FX_USDNGN_OFFICIAL_AVG_ANNUAL", "Naira per US dollar, official rate, annual average", "IMF IFS period-average official rate for Nigeria. NOT the parallel-market rate and NOT a daily rate.", "NGN per USD", "annual", "Official rate; calendar year; inverted by code from IMF USD per NGN"),
    ("FX_USDNGN_OFFICIAL_CLOSE_YEAREND", "Naira per US dollar, official rate, end of year", "IMF IFS end-of-period official rate for Nigeria. NOT the parallel-market rate.", "NGN per USD", "annual", "Official rate; 31 December; inverted by code from IMF USD per NGN"),
    ("FX_USDNGN_WB_AVG_ANNUAL_CHECK", "Naira per US dollar, World Bank period average (cross-check only)", "World Bank WDI PA.NUS.FCRF. Used only to cross-check the IMF average; never used in a report figure. It is built from the same IMF data, so it checks transcription and inversion, not independence.", "NGN per USD", "annual", "Official rate; calendar year"),
]


def sha256(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(con) -> dict:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    vid = now.strftime("%Y-%m-%d-") + f"{con.execute('SELECT count(*) FROM macro_vintages WHERE vintage_id LIKE ?', [now.strftime('%Y-%m-%d') + '%']).fetchone()[0] + 1:03d}"
    imf, wb = json.loads(IMF_FILE.read_text()), json.loads(WB_FILE.read_text())
    con.execute("INSERT INTO macro_vintages VALUES (?,?,?,?)", [vid, now, "fsa load-macro 0.1", "IMF IFS official rates (average and year-end) with World Bank average as a cross-check"])
    for row in CATALOG:
        con.execute("INSERT OR IGNORE INTO macro_series_catalog VALUES (?,?,?,?,?,?)", list(row))
    n = 0
    def put(series, year, value, src, path, note):
        nonlocal n
        con.execute("INSERT INTO macro_series VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    [series, date(year, 1, 1), date(year, 12, 31), value, "NGN per USD", vid, src["source_name"], src["source_url"],
                     datetime.fromisoformat(src["retrieved_at"].replace("Z", "")), sha256(path), "ok", note])
        n += 1
    for o in imf["observations"]:
        series = "FX_USDNGN_OFFICIAL_CLOSE_YEAREND" if o["transformation"] == "EOP_RT" else "FX_USDNGN_OFFICIAL_AVG_ANNUAL"
        put(series, o["year"], 1.0 / float(o["usd_per_ngn"]), imf, IMF_FILE, "Inverted by code: 1 / IMF USD per NGN")
    for o in wb["observations"]:
        put("FX_USDNGN_WB_AVG_ANNUAL_CHECK", o["year"], o["ngn_per_usd"], wb, WB_FILE, "Cross-check series")
    return {"vintage_id": vid, "rows": n}


def latest_vintage(con) -> str | None:
    r = con.execute("SELECT vintage_id FROM macro_vintages ORDER BY created_at DESC, vintage_id DESC LIMIT 1").fetchone()
    return r[0] if r else None


def rate(con, series_id: str, year: int, vintage: str | None = None) -> dict | None:
    """The rate for a year from the latest (or a named) vintage, with its source, or None: a missing rate is never zero and never guessed."""
    vintage = vintage or latest_vintage(con)
    r = con.execute("""SELECT value, vintage_id, source_name, source_url, source_sha256, quality_flag, retrieved_at FROM macro_series
                       WHERE series_id=? AND vintage_id=? AND period_end=make_date(?,12,31) AND value IS NOT NULL""", [series_id, vintage, year]).fetchone()
    return None if not r else dict(zip(("value", "vintage_id", "source_name", "source_url", "source_sha256", "quality_flag", "retrieved_at"), r))


def cross_check(con, vintage: str | None = None, tolerance: float = 0.005) -> list[dict]:
    """IMF average against the World Bank average, by year. A difference above the tolerance becomes a disclosure."""
    out = []
    for y in range(2021, 2026):
        a, b = rate(con, "FX_USDNGN_OFFICIAL_AVG_ANNUAL", y, vintage), rate(con, "FX_USDNGN_WB_AVG_ANNUAL_CHECK", y, vintage)
        if a and b:
            out.append({"year": y, "imf": a["value"], "world_bank": b["value"], "difference_pct": (a["value"] / b["value"] - 1) * 100, "within_tolerance": abs(a["value"] / b["value"] - 1) <= tolerance})
    return out
