"""Apply rules/ratings.yaml: Financial Health, Earnings Quality, Forensic Risk, data-quality grade and conclusion category.

Code only. Every rating is a count of named tests, and the result lists the tests that drove it and the tests that could not be run.
A test that cannot be computed (missing input, ratio not meaningful) is NOT TESTED: never passed, never failed, never zero.
"""
import ast
import json

import yaml

from fsa.analyse.ratios import NotMeaningful
from fsa.findings.triggers import TriggerEvaluator, build_context
from fsa.map.finalize import signed_off
from fsa.warehouse.db import ROOT

RULES = ROOT / "rules" / "ratings.yaml"
QUALITY_INPUTS = ("open_mapping_lines", "unsigned_mapping", "filing_arithmetic_notes", "pdf_quality_notes", "provider_differences", "peer_set_unchecked")
SCORE = {"critical": -1, "weak": 0, "adequate": 1, "strong": 2}


def load_rules() -> dict:
    return yaml.safe_load(RULES.read_text())


class Tester:
    """Evaluates rule expressions against one company-year, with extra names added as the rating proceeds."""

    def __init__(self, ctx, extra_inputs: dict):
        self.cur, self.prior, self.now, self.known, self.extra, self.lookup = ctx
        self.extra = dict(self.extra) | extra_inputs

    def add(self, **names):
        self.extra |= names

    def test(self, expr: str):
        """(True | False | None, reason). None means not tested."""
        ev = TriggerEvaluator(self.cur, self.prior, self.metrics, self.now, self.known, self.extra, self.lookup)
        try:
            return bool(ev.ev(ast.parse(expr, mode="eval").body)), ""
        except NotMeaningful as e:
            return None, str(e)

    metrics: dict = {}


def first_level(t: Tester, levels: list[dict], key: str) -> tuple[str | None, list[dict], int]:
    """Worst-to-best: the first level whose test is true. Returns (label, untested tests, how many non-default tests ran)."""
    untested, ran = [], 0
    for lv in levels:
        ok, why = t.test(lv["when"])
        if lv["when"] != "True":
            ran += ok is not None
        if ok is None:
            untested.append({"level": lv[key], "when": lv["when"], "reason": why})
        elif ok:
            return lv[key], untested, ran
    return None, untested, ran


def quality_inputs(con, ticker: str, fy: int, company_cfg: dict) -> dict:
    base = ROOT / "data" / "interim" / ticker / f"FY{fy}"
    notes = json.loads((base / "disclosures.json").read_text()) if (base / "disclosures.json").exists() else None
    count = lambda cat: None if notes is None else float(sum(n["category"] == cat for n in notes))
    peers = [c for c in company_cfg["companies"] if c.get("role") == "peer" and c.get("industry_peer_set") == next(
        x for x in company_cfg["companies"] if x["ticker"] == ticker)["industry_peer_set"]]
    return {
        "open_mapping_lines": float(con.execute("SELECT count(*) FROM mapping_final WHERE ticker=? AND fiscal_year=? AND status='open'", [ticker, fy]).fetchone()[0]),
        "unsigned_mapping": 0.0 if signed_off(ticker, fy) else 1.0,
        "filing_arithmetic_notes": count("filing_arithmetic"), "pdf_quality_notes": count("pdf_quality"),
        "provider_differences": float(con.execute("SELECT count(*) FROM reconciliation WHERE ticker=? AND fiscal_year=? AND status='differs'", [ticker, fy]).fetchone()[0]),
        "peer_set_unchecked": float(any(p.get("conflict_check") != "pass" for p in peers)),
        "auditor_adverse": None,          # needs the auditor's report read by code (rules A-01 to A-05): not built yet
    }


def rate_company_year(con, chart, ratio_rules, company_cfg: dict, ticker: str, fy: int) -> dict:
    R = load_rules()
    ctx = build_context(con, chart, ratio_rules, ticker, fy)
    t = Tester(ctx, quality_inputs(con, ticker, fy, company_cfg))
    Tester.metrics = ratio_rules["metrics"]
    out = {"ticker": ticker, "fiscal_year": fy, "rules_version": R["version"]}

    # 1. financial health
    fh, pillars = R["financial_health"], {}
    for name, p in fh["pillars"].items():
        label, untested, ran = first_level(t, p["levels"], "level")
        pillars[name] = {"level": label if ran else None, "untested": untested, "why": p["why"]}
    counts = {lv: sum(p["level"] == lv for p in pillars.values()) for lv in SCORE}
    n_rated = sum(p["level"] is not None for p in pillars.values())
    t.add(n_critical=counts["critical"], n_weak=counts["weak"], n_strong=counts["strong"], n_rated=n_rated)
    if n_rated < fh["min_pillars_rated"]:
        health = None
    else:
        health, _, _ = first_level(t, fh["overall"], "rating")
    out["financial_health"] = {"rating": health or "Not rated: insufficient data", "pillars": pillars, "pillars_rated": n_rated}

    # 2. earnings quality
    eq = R["earnings_quality"]
    results, points, tested = [], 0, 0
    for test in eq["tests"]:
        ok, why = t.test(test["when"])
        tested += ok is not None
        points += test["points"] if ok else 0
        results.append({"id": test["id"], "result": "not tested" if ok is None else ("adverse" if ok else "clear"), "points": test["points"] if ok else 0,
                        "why": test["why"], **({"reason": why} if ok is None else {})})
    t.add(points=points)
    label = first_level(t, eq["levels"], "rating")[0] if tested >= eq["min_tested"] else None
    out["earnings_quality"] = {"rating": label or "Not rated: insufficient data", "points": points, "tests_run": tested, "tests_total": len(eq["tests"]), "tests": results}

    # 3. forensic risk
    fr = R["forensic_risk"]
    label, untested, _ = first_level(t, fr["levels"], "rating")
    out["forensic_risk"] = {"rating": label or "Not rated: insufficient data", "untested": untested, "note": fr["note_for_report"],
                            "red_not_assignable": "The auditor's report is not read by code yet, so Red cannot be assigned." if fr["red_requires_auditor_tests"] else None}

    # 4. data quality
    label, untested, _ = first_level(t, R["data_quality"]["levels"], "grade")
    out["data_quality"] = {"grade": label, "untested": untested, "why": R["data_quality"]["why"], "inputs": {k: t.extra.get(k) for k in QUALITY_INPUTS}}

    # 5. conclusion category
    cn, trend = R["conclusion"], []
    tol = cn["trend_tolerance_pct"] / 100
    for m in cn["trend_measures"]:
        try:
            now_v, prev_v = t.now[m["name"]], t.lookup(m["name"], "prev")
            if t.now[m["name"]]["status"] != "ok":
                raise NotMeaningful("not meaningful this year")
            now_v = now_v["value"]
            moved = (now_v - prev_v) if m["better"] == "higher" else (prev_v - now_v)
            state = "improving" if moved > tol * abs(prev_v) else ("worsening" if moved < -tol * abs(prev_v) else "flat")
            trend.append({"measure": m["name"], "now": now_v, "previous": prev_v, "state": state})
        except (NotMeaningful, KeyError) as e:
            trend.append({"measure": m["name"], "state": "not tested", "reason": str(e)})
    if health is None:
        out["conclusion"] = {"category": "Not concluded: Financial Health is not rated", "trend": trend}
    else:
        t.add(health_distressed=float(health == "Distressed"), health_weak=float(health == "Weak"), health_ok=float(health in ("Strong", "Healthy")),
              n_improving=sum(x["state"] == "improving" for x in trend), n_worsening=sum(x["state"] == "worsening" for x in trend))
        out["conclusion"] = {"category": first_level(t, cn["levels"], "category")[0], "trend": trend}
    return out


def to_markdown(r: dict) -> str:
    L = [f"# Ratings: {r['ticker']} FY{r['fiscal_year']} (rules {r['rules_version']})", "",
         "> Draft ratings from rules/ratings.yaml. Counts of named tests, never a verdict. Educational analysis, not investment advice.", ""]
    fh = r["financial_health"]
    L += [f"## Financial Health: **{fh['rating']}** ({fh['pillars_rated']} of {len(fh['pillars'])} pillars rated)", ""]
    for name, p in fh["pillars"].items():
        L.append(f"- {name}: {p['level'] or 'not rated'}" + (f" (tests not run: {', '.join(u['when'] for u in p['untested'])})" if p["untested"] else ""))
    eq = r["earnings_quality"]
    L += ["", f"## Earnings Quality: **{eq['rating']}** ({eq['points']} demerit points; {eq['tests_run']} of {eq['tests_total']} tests run)", ""]
    L += [f"- {x['id']}: {x['result']}" + (f" (+{x['points']})" if x["points"] else "") + f" · {x['why']}" for x in eq["tests"] if x["result"] != "clear"] or ["- no adverse tests"]
    fr = r["forensic_risk"]
    L += ["", f"## Forensic Risk: **{fr['rating']}**", "", f"- {fr['note']}", f"- {fr['red_not_assignable']}" if fr["red_not_assignable"] else ""]
    dq = r["data_quality"]
    L += ["", f"## Data quality: **{dq['grade']}**", "", f"- inputs: {dq['inputs']}"]
    cn = r["conclusion"]
    L += ["", f"## Conclusion category: **{cn['category']}**", ""] + [f"- {x['measure']}: {x['state']}" + (f" ({x['previous']:.2f} to {x['now']:.2f})" if "now" in x else "") for x in cn["trend"]]
    return "\n".join(l for l in L if l is not None) + "\n"
