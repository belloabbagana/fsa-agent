"""USD view of a company-year: naira statements translated at the official rate (information only, nothing restated for inflation).

Income-statement and cash-flow rows use the annual-average rate of their year; balance-sheet rows use the year-end rate (macro interface section 5).
A missing rate gives 'not available' for that row, never zero. The report names the rates, their source and the vintage used; a vintage older than 90 days is flagged, never blocked.
"""
from datetime import datetime

from fsa.analyse.ratios import NotMeaningful
from fsa.findings.triggers import TriggerEvaluator, build_context
from fsa.macro import load as macro
import ast

SERIES = {"avg": "FX_USDNGN_OFFICIAL_AVG_ANNUAL", "close": "FX_USDNGN_OFFICIAL_CLOSE_YEAREND"}
MAX_AGE_DAYS = 90


def usd_view(con, chart, ratio_rules, layout: dict, ticker: str, fy: int, years: int = 3, today: datetime | None = None) -> dict:
    vintage = macro.latest_vintage(con)
    if not vintage:
        return {"status": "not available", "reason": "no macro vintage is loaded (run: ./fsa load-macro)"}
    today = today or datetime.now()
    ys, ctx, rates = [], {}, {}
    for y in range(fy - years + 1, fy + 1):
        try:
            cur, prior, now, known, extra, lookup = build_context(con, chart, ratio_rules, ticker, y)
        except SystemExit:
            continue
        ctx[y] = TriggerEvaluator(cur, prior, ratio_rules["metrics"], now, known, extra, lookup)
        rates[y] = {b: macro.rate(con, s, y, vintage) for b, s in SERIES.items()}
        ys.append(y)
    rows = []
    for r in layout["rows"]:
        cells = {}
        for y in ys:
            rate = rates[y][r["basis"]]
            try:
                n = ctx[y].ev(ast.parse(r["expr"], mode="eval").body)
            except (NotMeaningful, KeyError, ValueError) as e:
                cells[str(y)] = {"usd_m": None, "note": f"n/a: {e}"}
                continue
            cells[str(y)] = {"usd_m": n / rate["value"], "naira_m": n} if rate else {"usd_m": None, "note": f"n/a: no {r['basis']} rate for {y} in vintage {vintage}"}
        rows.append({"label": r["label"], "expr": r["expr"], "basis": r["basis"], "values": cells})
    growth = {}
    for key in ("PL_REVENUE", "PL_PROFIT_FOR_YEAR"):
        row = next((r for r in rows if r["expr"] == key), None)
        if row:
            for y in ys[1:]:
                a, b = row["values"].get(str(y - 1)), row["values"].get(str(y))
                if a and b and a.get("usd_m") and b.get("usd_m") and a["naira_m"] > 0 and a["usd_m"] > 0:
                    growth.setdefault(key, {})[str(y)] = {"naira_pct": (b["naira_m"] / a["naira_m"] - 1) * 100, "usd_pct": (b["usd_m"] / a["usd_m"] - 1) * 100}
    used = {s: {str(y): rates[y][b]["value"] if rates[y][b] else None for y in ys} for b, s in SERIES.items()}
    anyrate = next((rates[y][b] for y in ys for b in SERIES if rates[y][b]), None)
    age = (today - anyrate["retrieved_at"]).days if anyrate else None
    return {"status": "ok", "vintage_id": vintage, "years": ys, "rates": used, "rows": rows, "growth": growth,
            "source": anyrate["source_name"] if anyrate else None, "source_url": anyrate["source_url"] if anyrate else None,
            "raw_file_sha256": anyrate["source_sha256"] if anyrate else None, "retrieved_at": str(anyrate["retrieved_at"]) if anyrate else None,
            "vintage_age_days": age, "vintage_older_than_limit": bool(age is not None and age > MAX_AGE_DAYS),
            "cross_check": macro.cross_check(con, vintage)}


def to_markdown(v: dict) -> list[str]:
    if v["status"] != "ok":
        return [f"USD view not available: {v['reason']}.", ""]
    ys = v["years"]
    L = ["| USD millions | " + " | ".join(f"FY{y}" for y in ys) + " | Rate basis |", "|---|" + "---:|" * len(ys) + "---|"]
    for r in v["rows"]:
        L.append(f"| {r['label']} | " + " | ".join(("n/a" if r["values"][str(y)].get("usd_m") is None else f"{r['values'][str(y)]['usd_m']:,.0f}") for y in ys) + f" | {'annual average' if r['basis'] == 'avg' else 'year-end'} |")
    avg, close = v["rates"][SERIES["avg"]], v["rates"][SERIES["close"]]
    L += ["", "| Official rate (₦ per US$) | " + " | ".join(f"FY{y}" for y in ys) + " |", "|---|" + "---:|" * len(ys),
          "| Annual average | " + " | ".join("n/a" if avg[str(y)] is None else f"{avg[str(y)]:,.1f}" for y in ys) + " |",
          "| Year-end | " + " | ".join("n/a" if close[str(y)] is None else f"{close[str(y)]:,.1f}" for y in ys) + " |", ""]
    for key, label in (("PL_REVENUE", "Revenue"), ("PL_PROFIT_FOR_YEAR", "Profit for the year")):
        g = v["growth"].get(key)
        if g:
            L.append(f"- {label} growth, naira against dollars: " + "; ".join(f"FY{y} {x['naira_pct']:+,.1f}% in naira, {x['usd_pct']:+,.1f}% in dollars" for y, x in sorted(g.items())) + ".")
    L += ["", f"*Official rate (IMF International Financial Statistics, inverted by code; the average agrees with the World Bank series, which is built from the same IMF data). Vintage {v['vintage_id']}, retrieved {v['retrieved_at'][:10]}, raw file SHA-256 {v['raw_file_sha256'][:12]}…. "
          "Income and cash-flow rows at the annual-average rate, balance-sheet rows at the year-end rate. Nominal naira converted at the official rate: not restated for inflation, and parallel-market rates are not used.*"
          + (" **This vintage is older than 90 days.**" if v["vintage_older_than_limit"] else ""), ""]
    bad = [c for c in v["cross_check"] if not c["within_tolerance"]]
    if bad:
        L.append("*Cross-check: the IMF and World Bank averages differ by more than 0.5% in " + ", ".join(f"{c['year']} ({c['difference_pct']:+.2f}%)" for c in bad) + ".*")
        L.append("")
    return L
