"""Dry-run trigger engine: which finding rules fire for a company-year. Code decides; no model is involved.

A rule fires, does not fire, or is NOT TESTED (an input is missing or not meaningful). Missing never means "no finding".
The rules live in rules/findings/*.yaml. Names follow rules/findings/README.md.
"""
import ast
import json
import re
from datetime import date

import yaml

from fsa.analyse.ratios import Evaluator, NotMeaningful, period_values
from fsa.warehouse.db import ROOT

RULES_DIR = ROOT / "rules" / "findings"
SUFFIX = re.compile(r"^(?P<base>[a-z][a-z0-9_]*?)_(?P<kind>prev2|prev|cmp|peer_median|peer_min|peer_max|peer_n)$")


def load_rules(directory=RULES_DIR) -> list[dict]:
    rules = []
    for f in sorted(directory.glob("*.yaml")):
        for r in yaml.safe_load(f.read_text())["rules"]:
            r["file"] = f.name
            rules.append(r)
    return rules


class TriggerEvaluator(Evaluator):
    """Same restricted evaluator, with the extra names a finding rule may use."""

    def __init__(self, cur, prior, metrics, ratios_now, known, extra, lookup):
        super().__init__(cur, prior, metrics, ratios_now, known, strict=False)
        self.extra, self.lookup, self.used = extra, lookup, {}

    def name(self, n: str, period: str):
        if n in self.extra:
            v = self.extra[n]
            if v is None:
                raise NotMeaningful(f"{n} is not available for this company-year")
            self.used[n] = v
            return v
        m = SUFFIX.match(n) if n.islower() else None
        if m:
            v = self.lookup(m["base"], m["kind"])
            self.used[n] = v
            return v
        try:
            v = super().name(n, period)
        except ValueError:
            raise NotMeaningful(f"{n} is not available in the warehouse")
        if n.islower() or n in self.metrics:
            self.used[n] = v
        return v


def _one(con, sql, args):
    row = con.execute(sql, args).fetchone()
    return row


def build_context(con, chart: dict, ratio_rules: dict, ticker: str, fy: int):
    cur = period_values(con, chart, ticker, fy, date(fy, 12, 31))
    if cur is None:
        raise SystemExit(f"No settled mapping for {ticker} FY{fy}. Run finalize-mapping first.")
    prior = period_values(con, chart, ticker, fy, date(fy - 1, 12, 31))

    def ratios_for(year: int) -> dict:
        out = {}
        for rid, status, value, reason in con.execute("SELECT ratio_id, status, value, reason FROM ratios WHERE ticker=? AND fiscal_year=?", [ticker, year]).fetchall():
            out[rid] = {"status": status, "value": value, "reason": reason}
        for rid, status, value, reason in con.execute("SELECT item_id, status, value, reason FROM forensic_scores WHERE ticker=? AND fiscal_year=?", [ticker, year]).fetchall():
            out[rid] = {"status": status, "value": value, "reason": reason}
        return out

    now, hist = ratios_for(fy), {"prev": ratios_for(fy - 1), "prev2": ratios_for(fy - 2)}

    def lookup(base: str, kind: str):
        if kind in ("prev", "prev2"):
            r = hist[kind].get(base)
            if r is None:
                raise NotMeaningful(f"{base} is not computed for FY{fy - (1 if kind == 'prev' else 2)}")
            if r["status"] != "ok":
                raise NotMeaningful(f"{base} for an earlier year is not meaningful ({r['reason']})")
            return r["value"]
        if kind == "cmp":                          # the company's own value on the comparison basis, as stored with its peer position
            row = _one(con, "SELECT value FROM peer_position WHERE ticker=? AND fiscal_year=? AND ratio_id=?", [ticker, fy, base])
            if row is None or row[0] is None:
                raise NotMeaningful(f"{base} is not available on the peer-comparison basis")
            return row[0]
        col = {"peer_median": "peer_median", "peer_min": "peer_min", "peer_max": "peer_max", "peer_n": "peer_n"}[kind]
        row = _one(con, f"SELECT {col} FROM peer_position WHERE ticker=? AND fiscal_year=? AND ratio_id=?", [ticker, fy, base])
        if row is None or row[0] is None:
            raise NotMeaningful(f"no peer figure for {base}")
        return row[0]

    chk = lambda y: con.execute("SELECT count(*), sum(CASE WHEN status='fail' THEN 1 ELSE 0 END) FROM integrity_checks WHERE ticker=? AND fiscal_year=? AND check_name LIKE 'comparative:%'", [ticker, y]).fetchone()
    n_now, fail_now = chk(fy)
    n_next, fail_next = chk(fy + 1)
    flags = con.execute("SELECT count(*), sum(CASE WHEN flagged THEN 1 ELSE 0 END) FROM forensic_scores WHERE ticker=? AND fiscal_year=? AND flagged IS NOT NULL", [ticker, fy]).fetchone()
    extra = {
        "restated_lines": float(fail_now) if n_now else None,
        "later_restated_lines": float(fail_next) if n_next else None,
        "flags_tripped": float(flags[1] or 0) if flags[0] else None,
        "has_acquisition": 1.0 if abs(cur.get("CF_ACQUISITIONS", 0.0)) > 0 else 0.0,
    }
    known = {a["code"] for a in chart["accounts"]}
    return cur, prior, now, known, extra, lookup


def _threshold(text: str) -> tuple[str, float]:
    m = re.match(r"^\s*(>=|>)\s*(-?[\d.]+)\s*$", text)
    return m[1], float(m[2])


def rate(spec: dict, ev: TriggerEvaluator) -> tuple[str, float | None, str]:
    """Materiality for a fired rule: (level, measure, note)."""
    if "fixed" in spec:
        level, note = spec["fixed"], ""
        cond = spec.get("escalate_if")
        if cond and level != "high":
            try:
                if ev.ev(ast.parse(cond, mode="eval").body):
                    level, note = "high", f"raised to high because {cond}"
            except NotMeaningful as e:
                note = f"escalation not tested ({e})"
        return level, None, note
    try:
        measure = ev.ev(ast.parse(spec["measure"], mode="eval").body)
    except NotMeaningful as e:
        return "unrated", None, f"materiality measure not available ({e})"
    for level in ("high", "medium"):
        op, limit = _threshold(spec[level])
        if measure >= limit if op == ">=" else measure > limit:
            return level, measure, ""
    return "low", measure, ""


def text_rule(rule: dict, rep: dict | None) -> dict:
    """State and evidence for a text rule, from the auditor's report read by code. Every quote was verified against the PDF text when it was read.
    Rules about the notes (A-05) stay NOT TESTED until the notes are read."""
    if rule["id"] == "A-05":
        return {"reason": "the engine does not read the notes yet"}
    if rep is None:
        return {"reason": "the auditor's report has not been read (run: ./fsa read-auditor-report)"}
    op = rep["opinion"]
    if rule["id"] == "A-01":
        if op["type"] == "unclear":
            return {"reason": "the opinion paragraph could not be classified by code"}
        if op["type"] == "unmodified":
            return {"state": "not fired"}
        return {"state": "fired", "evidence": [{"section": "Opinion", "page": op["page"], "quote": op["quote"]}]}
    if rule["id"] == "A-02":
        gc = rep["going_concern"]
        return {"state": "fired", "evidence": gc["evidence"]} if gc["material_uncertainty_section"] else {"state": "not fired"}
    if rule["id"] == "A-03":
        ev = rep["emphasis_of_matter"] + rep["other_matter"]
        return {"state": "fired", "evidence": ev} if ev else {"state": "not fired"}
    if rule["id"] == "A-04":
        k = rep["key_audit_matters"]
        if not k["section_present"]:
            return {"state": "not fired"}
        ev = [{"section": "Key audit matters", "page": t.get("page") or k["page"], "quote": t["quote"], "title": t["title"]} for t in k["titles"]]
        return {"state": "fired", "evidence": ev or [{"section": "Key audit matters", "page": k["page"], "quote": "(a Key audit matters section is present; titles not listed)"}]}
    return {"reason": f"no reader for text rule {rule['id']}"}


def evaluate_rules(con, chart, ratio_rules, rules, ticker, fy) -> list[dict]:
    cur, prior, now, known, extra, lookup = build_context(con, chart, ratio_rules, ticker, fy)
    apath = ROOT / "data" / "interim" / ticker / f"FY{fy}" / "auditor_report.json"
    auditor = json.loads(apath.read_text()) if apath.exists() else None
    out = []
    for r in rules:
        res = {"rule_id": r["id"], "title": r["title"], "section": r["section"], "state": "not tested", "materiality": None, "measure": None, "reason": "", "facts": "{}"}
        if r.get("kind") == "text":
            res.update(text_rule(r, auditor))
            if res["state"] == "fired":
                res["materiality"], res["measure"], res["reason"] = rate(r["materiality"], TriggerEvaluator(cur, prior, ratio_rules["metrics"], now, known, extra, lookup))
            out.append(res)
            continue
        ev = TriggerEvaluator(cur, prior, ratio_rules["metrics"], now, known, extra, lookup)
        try:
            fired = ev.ev(ast.parse(r["trigger"], mode="eval").body)
            res["state"] = "fired" if fired else "not fired"
            if fired:
                res["materiality"], res["measure"], res["reason"] = rate(r["materiality"], ev)
        except NotMeaningful as e:
            res["reason"] = str(e)
        res["facts"] = json.dumps(ev.used | ev.trace, sort_keys=True, default=str)
        out.append(res)
    return out


def save(con, ticker, fy, results, version) -> None:
    con.execute("DELETE FROM finding_candidates WHERE ticker=? AND fiscal_year=?", [ticker, fy])
    for r in results:
        con.execute("INSERT INTO finding_candidates (ticker, fiscal_year, rule_id, title, section, state, materiality, measure, reason, facts, rules_version) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    [ticker, fy, r["rule_id"], r["title"], r["section"], r["state"], r["materiality"], r["measure"], r["reason"], r["facts"], version])
