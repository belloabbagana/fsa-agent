"""Evaluate the ratio definitions in rules/ratios.yaml. Code does all the arithmetic; the rules decide what a ratio means.

Formulas are tiny arithmetic expressions over chart-of-accounts codes. They are parsed with Python's ast module and
evaluated by a restricted evaluator that only allows numbers, names, + - * /, comparisons, and avg() / prior().
Anything else is rejected, so a rule file can never run arbitrary code.
"""
import ast
import json
from datetime import date

import yaml

from fsa.map.validate_mapping import account_totals, computed
from fsa.warehouse.db import ROOT

RULES_PATH = ROOT / "rules" / "ratios.yaml"
FACTOR = {"NGN_million": 1.0, "NGN_thousand": 0.001}


class NotMeaningful(Exception):
    pass


def load_rules(path=RULES_PATH) -> dict:
    return yaml.safe_load(path.read_text())


class Evaluator:
    def __init__(self, cur: dict, prior: dict | None, metrics: dict, ratios_done: dict, known_codes: set, strict: bool = False):
        self.strict = strict                                   # strict: a code the data source did not provide is not meaningful
        self.vals = {"cur": cur, "prior": prior}
        self.metrics, self.ratios, self.known = metrics, ratios_done, known_codes
        self.cache, self.trace = {}, {}

    # ---- names
    def name(self, n: str, period: str):
        if n in self.ratios:                                     # a ratio computed earlier (lower-case id)
            r = self.ratios[n]
            if r["status"] != "ok":
                raise NotMeaningful(f"depends on {n}, which is not meaningful ({r['reason']})")
            return r["value"]
        if n in self.metrics:
            key = (n, period)
            if key not in self.cache:
                m = self.metrics[n]
                conds = [ast.parse(c, mode="eval").body for c in m.get("nm_if", [])]
                about_self = [c for c in conds if any(isinstance(x, ast.Name) and x.id == n for x in ast.walk(c))]
                for c, text in zip(conds, m.get("nm_if", [])):          # checks that do not need the result come first
                    if c not in about_self and self.ev(c, period):
                        raise NotMeaningful(f"{n}: {text}")
                value = self.ev(ast.parse(m["formula"], mode="eval").body, period)
                self.cache[key] = value                                   # now the result exists for checks about itself
                for c, text in zip(conds, m.get("nm_if", [])):
                    if c in about_self and self.ev(c, period):
                        del self.cache[key]
                        raise NotMeaningful(f"{n}: {text}")
            return self.cache[key]
        if n in self.known:                                      # an account code
            side = self.vals[period]
            if side is None:
                raise NotMeaningful("no opening balance available")
            if self.strict and n not in side:
                raise NotMeaningful(f"{n} is not provided by the data source for this company and year")
            v = side.get(n, 0.0)                                 # a line the company does not print is structurally nil
            self.trace[f"{n}[{period}]"] = round(v, 3)
            return v
        raise ValueError(f"unknown name in a rule: {n}")

    # ---- expressions
    def ev(self, node, period="cur"):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.Name):
            return self.name(node.id, period)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            v = self.ev(node.operand, period)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            a, b = self.ev(node.left, period), self.ev(node.right, period)
            if isinstance(node.op, ast.Add):
                return a + b
            if isinstance(node.op, ast.Sub):
                return a - b
            if isinstance(node.op, ast.Mult):
                return a * b
            if b == 0:
                raise NotMeaningful("the denominator is zero")
            return a / b
        if isinstance(node, ast.Compare):
            left, ok = self.ev(node.left, period), True
            for op, comp in zip(node.ops, node.comparators):
                right = self.ev(comp, period)
                ok = ok and {ast.Lt: left < right, ast.LtE: left <= right, ast.Gt: left > right, ast.GtE: left >= right}[type(op)]
                left = right
            return ok
        if isinstance(node, ast.BoolOp):
            vals = [self.ev(v, period) for v in node.values]
            return all(vals) if isinstance(node.op, ast.And) else any(vals)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and len(node.args) == 1 and not node.keywords:
            arg = node.args[0]
            if node.func.id == "prior":
                return self.ev(arg, "prior")
            if node.func.id == "avg":
                return (self.ev(arg, "cur") + self.ev(arg, "prior")) / 2
        raise ValueError(f"expression not allowed in a rule: {ast.dump(node)[:80]}")


def period_values(con, chart: dict, ticker: str, fy: int, period_end: date) -> dict | None:
    """Group account values at a period end, in N millions, from the settled mapping (line accounts and chart subtotals)."""
    rows = con.execute("""SELECT m.code, p.statement, p.seq, p.value, p.unit FROM mapping_final m
        JOIN pdf_statement_lines p ON p.ticker=m.ticker AND p.fiscal_year=m.fiscal_year AND p.statement=m.statement AND p.seq=m.seq
        WHERE m.ticker=? AND m.fiscal_year=? AND m.code IS NOT NULL AND p.entity='group' AND p.period_end=?""",
                       [ticker, fy, period_end]).fetchall()
    if not rows:
        return None
    mapping, values = {}, {}
    for code, st, seq, v, unit in rows:
        if unit not in FACTOR or v is None:
            continue                                             # per-share lines are not amounts
        mapping[(st, seq)] = code
        values[(st, seq)] = v * FACTOR[unit]
    totals = account_totals(chart, mapping, values)
    out = {a["code"]: computed(chart, a["code"], totals) for a in chart["accounts"]}
    return out


def run_ratios(rules: dict, chart: dict, cur: dict, prior: dict | None, strict: bool = False, overrides: dict | None = None) -> list[dict]:
    known = {a["code"] for a in chart["accounts"]}
    done: dict = {}
    results = []
    for r in rules["ratios"]:
        ov = (overrides or {}).get(r["id"], {})
        formula, conditions = ov.get("formula", r["formula"]), ov.get("nm_if", r.get("nm_if", []))
        ev = Evaluator(cur, prior, rules["metrics"], done, known, strict)
        res = {"ratio_id": r["id"], "category": r.get("category"), "unit": r.get("unit"), "value": None, "status": "ok", "reason": ""}
        try:
            for cond in conditions:
                if ev.ev(ast.parse(cond, mode="eval").body):
                    raise NotMeaningful(cond.replace("<=", "is not above").replace(">=", "is not below"))
            res["value"] = ev.ev(ast.parse(formula, mode="eval").body)
        except NotMeaningful as e:
            res.update(status="N/M", reason=str(e))
        res["inputs"] = json.dumps(ev.trace, sort_keys=True)
        done[r["id"]] = res
        results.append(res)
    return results


def compute(con, chart: dict, rules: dict, ticker: str, fy: int) -> list[dict]:
    cur = period_values(con, chart, ticker, fy, date(fy, 12, 31))
    if cur is None:
        raise SystemExit(f"No settled mapping for {ticker} FY{fy}. Run finalize-mapping first.")
    prior = period_values(con, chart, ticker, fy, date(fy - 1, 12, 31))
    return run_ratios(rules, chart, cur, prior)


def save(con, ticker, fy, results, version) -> None:
    con.execute("DELETE FROM ratios WHERE ticker=? AND fiscal_year=?", [ticker, fy])
    for r in results:
        con.execute("INSERT INTO ratios (ticker, fiscal_year, ratio_id, category, unit, value, status, reason, inputs, rules_version) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [ticker, fy, r["ratio_id"], r["category"], r["unit"], r["value"], r["status"], r["reason"], r["inputs"], version])


def fmt(r: dict) -> str:
    if r["status"] != "ok":
        return f"N/M ({r['reason']})"
    return {"pct": f"{r['value']:,.1f}%", "x": f"{r['value']:,.2f}x", "days": f"{r['value']:,.0f} days"}.get(r["unit"], f"{r['value']:,.2f}")
