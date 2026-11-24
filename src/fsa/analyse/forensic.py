"""Forensic scores: Beneish M-score, Altman Z'' (emerging markets), growth-gap flags. Code does the arithmetic.

The formulas live in rules/forensic.yaml and run through the same restricted evaluator as the ratios. This module
adds the published zones (bands) and the red-flag thresholds, and stores the results with their inputs.
These are indicators to examine, never findings on their own.
"""
import ast
from datetime import date

import yaml

from fsa.analyse.ratios import Evaluator, NotMeaningful, period_values, run_ratios
from fsa.warehouse.db import ROOT

FORENSIC_PATH = ROOT / "rules" / "forensic.yaml"


def load_rules(path=FORENSIC_PATH) -> dict:
    return yaml.safe_load(path.read_text())


def merged(ratio_rules: dict, f_rules: dict) -> dict:
    """The ordinary ratios first, then the forensic items, so a forensic formula may use any ratio by its id."""
    return {"metrics": ratio_rules["metrics"], "ratios": ratio_rules["ratios"] + f_rules["ratios"], "version": ratio_rules.get("version")}


def band_for(item: dict, value: float) -> str | None:
    for b in item.get("bands", []):
        if "above" not in b or value > b["above"]:
            return b["label"]
    return None


def evaluate(chart: dict, ratio_rules: dict, f_rules: dict, cur: dict, prior: dict | None, strict: bool = False) -> list[dict]:
    all_rules = merged(ratio_rules, f_rules)
    results = {r["ratio_id"]: r for r in run_ratios(all_rules, chart, cur, prior, strict=strict)}
    known = {a["code"] for a in chart["accounts"]}
    out = []
    for item in f_rules["ratios"]:
        r = dict(results[item["id"]])
        r["band"], r["flagged"] = None, None
        if r["status"] == "ok":
            r["band"] = band_for(item, r["value"])
            if item.get("flag_if"):
                done = {k: v for k, v in results.items()}
                try:
                    r["flagged"] = bool(Evaluator(cur, prior, all_rules["metrics"], done, known, strict).ev(ast.parse(item["flag_if"], mode="eval").body))
                except NotMeaningful:
                    r["flagged"] = None
        out.append(r)
    return out


def compute(con, chart: dict, ratio_rules: dict, f_rules: dict, ticker: str, fy: int) -> list[dict]:
    cur = period_values(con, chart, ticker, fy, date(fy, 12, 31))
    if cur is None:
        raise SystemExit(f"No settled mapping for {ticker} FY{fy}. Run finalize-mapping first.")
    prior = period_values(con, chart, ticker, fy - 1, date(fy - 1, 12, 31)) if False else period_values(con, chart, ticker, fy, date(fy - 1, 12, 31))
    return evaluate(chart, ratio_rules, f_rules, cur, prior)


def save(con, ticker: str, fy: int, results: list[dict], version: str) -> None:
    con.execute("DELETE FROM forensic_scores WHERE ticker=? AND fiscal_year=?", [ticker, fy])
    for r in results:
        con.execute("INSERT INTO forensic_scores VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [ticker, fy, r["ratio_id"], r["unit"], r["value"], r["status"], r["reason"], r["band"], r["flagged"], r["inputs"], version])


def fmt(r: dict) -> str:
    if r["status"] != "ok":
        return f"N/M ({r['reason']})"
    v = r["value"]
    s = {"pp": f"{v:+,.1f} points", "x": f"{v:,.2f}x", "ratio": f"{v:,.3f}", "score": f"{v:,.2f}"}.get(r["unit"], f"{v:,.2f}")
    return s + (f"  → {r['band']}" if r.get("band") else "") + ("  [FLAG]" if r.get("flagged") else "")


def contributions(item: dict, results: list[dict]) -> list[tuple[str, float, float]]:
    """What drives a score: [(component, value, coefficient x value)], largest effect first. Constant excluded."""
    co = item.get("coefficients") or {}
    byid = {r["ratio_id"]: r for r in results}
    rows = [(k, byid[k]["value"], c * byid[k]["value"]) for k, c in co.items()
            if k != "constant" and k in byid and byid[k]["status"] == "ok"]
    return sorted(rows, key=lambda t: -abs(t[2]))
