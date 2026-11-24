"""Settle each printed line's chart code from the rule proposal, the model proposal and the analyst's decisions.

Order of authority:
  1. an APPROVED analyst decision for that label                       -> analyst_decision
  2. rule and model give the same code                                 -> agreed
  3. rule has a code and no model was run                              -> rule_only
     rule has a code and the model gave NO code (abstained)            -> rule_model_silent (the rule stands)
  4. rule has no code (an unlabelled total) and the model suggests a
     subtotal whose sum of mapped parts reproduces that very line      -> model_accepted
  5. rule and model disagree, no approved decision                     -> rule_disputed (rule code used provisionally, flagged OPEN)
  6. nothing usable                                                    -> open
A company-year is only 'approved' when the analyst has signed it off in config/mapping_signoff.yaml AND nothing is open.
"""
from pathlib import Path

import yaml

from fsa.extract.pdf_statements import norm_label
from fsa.map.validate_mapping import account_totals, computed
from fsa.warehouse.db import ROOT

DECISIONS_PATH = ROOT / "config" / "mapping_decisions.yaml"
SIGNOFF_PATH = ROOT / "config" / "mapping_signoff.yaml"


def stmt_group(statement: str) -> str:
    return "profit_part" if statement in {"profit_or_loss", "comprehensive_income"} else statement


def load_decisions(path: Path = DECISIONS_PATH) -> list[dict]:
    return (yaml.safe_load(path.read_text()) or {}).get("decisions", [])


def decision_index(decisions: list[dict], approved_only: bool = True) -> dict:
    return {(stmt_group(d["statement"]), norm_label(d["label"])): d for d in decisions
            if not approved_only or d.get("status") == "approved"}


def finalize(chart: dict, lines: list[dict], rule: dict, model: dict | None, decisions: list[dict], tol: float = 2.0) -> list[dict]:
    """lines: dicts with statement, seq, label, value. rule/model: {(statement, seq): code or None}."""
    approved = decision_index(decisions, approved_only=True)
    proposed = decision_index(decisions, approved_only=False)
    out = []
    for ln in lines:
        k = (ln["statement"], ln["seq"])
        r, m = rule.get(k), (model or {}).get(k)
        dkey = (stmt_group(ln["statement"]), norm_label(ln["label"]))
        row = {"statement": ln["statement"], "seq": ln["seq"], "label": ln["label"], "rule_code": r, "model_code": m,
               "code": None, "basis": "open", "status": "open", "note": ""}
        if dkey in approved:
            row.update(code=approved[dkey]["code"], basis="analyst_decision", status="settled", note=approved[dkey].get("reason", ""))
        elif r and model is None:
            row.update(code=r, basis="rule_only", status="settled")
        elif r and m == r:
            row.update(code=r, basis="agreed", status="settled")
        elif r and m is None:
            row.update(code=r, basis="rule_model_silent", status="settled", note="the model left this line unmapped; the rule's code stands")
        elif r and m != r:
            pending = proposed.get(dkey)
            row.update(code=r, basis="rule_disputed", status="open",
                       note=f"model says {m or 'no code'}" + (f"; a proposed decision awaits approval ({pending['code']})" if pending else ""))
        elif not r and not ln["label"].strip() and not ln.get("value"):
            row.update(basis="intermediate_total", status="settled",
                       note="unlabelled row with no figure for this year (a blank or nil placeholder): nothing to map")
        elif not r and m:
            row.update(code=None, basis="open", status="open", note=f"model suggests {m}")
        elif not ln["label"].strip():
            row.update(basis="intermediate_total", status="settled",
                       note="unlabelled running total with no chart account: not used; the sums around it are checked by code")
        else:
            row.update(basis="open", status="open", note="no code from rule or model")
        out.append(row)

    # step 4: unlabelled totals the model recognised, accepted only if the chart's own sum reproduces the printed figure
    accts = {a["code"]: a for a in chart["accounts"]}
    mapping = {(r["statement"], r["seq"]): r["code"] for r in out if r["code"]}
    values = {(l["statement"], l["seq"]): l["value"] for l in lines}
    totals = account_totals(chart, mapping, values)
    for r in out:
        m = r["model_code"]
        if r["status"] == "open" and not r["rule_code"] and m in accts and accts[m]["kind"] == "subtotal":
            v = values.get((r["statement"], r["seq"]))
            if v is not None and abs(v - computed(chart, m, totals)) <= tol:
                r.update(code=m, basis="model_accepted", status="settled", note="unlabelled total: the model's code reproduces the printed figure")
    return out


def signed_off(ticker: str, fy: int, path: Path = SIGNOFF_PATH) -> dict | None:
    data = (yaml.safe_load(path.read_text()) or {}).get("signoff", {}) or {}
    return (data.get(ticker) or {}).get(f"FY{fy}")
