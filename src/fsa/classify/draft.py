"""Draft a GICS classification for each company with a model, check the names by code, and write config/gics_map.yaml.

Everything is written as status 'proposed'. Nothing counts until the analyst approves it (rule: Bello decides).
Only public facts go to the model: the company's name and the sector label NGN Market gives it.
"""
import json
import re

import yaml

from fsa.classify.gics_structure import check, structure_text
from fsa.map.model_proposer import ask, verify_model
from fsa.warehouse.db import ROOT

SYSTEM = ("You classify listed companies into the GICS hierarchy. Answer with a JSON array only, no prose, no code fences. "
          "For every company give: ticker, sector, industry_group, industry, sub_industry, confidence (high, medium or low), "
          "reason (at most 25 words, say what the company does). Use ONLY sector, industry group and industry names from the list given, "
          "spelled exactly, nested correctly. sub_industry must be a real GICS sub-industry name. "
          "Judge by what the company actually does, not by the label of the local exchange.")
CLASSIFICATION_FILE = ROOT / "data" / "raw" / "ngnmarket" / "ngx_company_classification_2026-11-23.json"
OUT = ROOT / "config" / "gics_map.yaml"


def load_ngx_labels() -> dict:
    data = json.loads(CLASSIFICATION_FILE.read_text())["companies"]
    return {c["symbol"]: c for c in data}


def build_prompt(companies: list[dict]) -> str:
    rows = "\n".join(f"{c['ticker']} | {c['name']} | local exchange label: {c['sector']} / {c['sub_sector']}" for c in companies)
    return f"GICS STRUCTURE (sector > industry group > industries):\n{structure_text()}\n\nCOMPANIES (ticker | name | label):\n{rows}\n\nReturn the JSON array now."


def draft(companies: list[dict], model: str) -> tuple[list[dict], set, list[str]]:
    ans = ask(build_prompt(companies), model, system=SYSTEM)
    if ans.is_error:
        raise RuntimeError(f"model call failed: {ans.error_text}")
    used = verify_model(ans, model)
    m = re.search(r"\[.*\]", ans.text, re.S)
    items, problems = [], []
    for it in (json.loads(m.group(0)) if m else []):
        why = check(it.get("sector"), it.get("industry_group"), it.get("industry"))
        if why:
            problems.append(f"{it.get('ticker')}: {why}: rejected")
            continue
        items.append(it)
    return items, used, problems


def write_yaml(items: list[dict], companies: list[dict], used: set, problems: list[str], path=OUT) -> None:
    by = {c["ticker"]: c for c in companies}
    out = {}
    for it in items:
        c = by[it["ticker"]]
        out[it["ticker"]] = {
            "name": c["name"], "sector": it["sector"], "industry_group": it["industry_group"], "industry": it["industry"],
            "sub_industry": it["sub_industry"], "sub_industry_checked": False, "confidence": it["confidence"], "reason": it["reason"],
            "exchange_label": f"{c['sector']} / {c['sub_sector']}", "proposed_by": ", ".join(sorted(used)), "status": "proposed", "approved_by": None,
        }
    head = ("# GICS CLASSIFICATION per company: DRAFT by a model, for Bello to approve (status: proposed -> approved).\n"
            "# Sector, industry group and industry names are checked by code against the GICS list; sub-industry is as drafted (unchecked).\n"
            "# Peer comparisons use the INDUSTRY level (and sub-industry where it changes the analysis). Only approved rows are used.\n"
            "# To approve: change status to approved and set approved_by. To change a classification: edit it, then approve.\n")
    if problems:
        head += "# Rejected by the checker (not listed below): " + "; ".join(problems) + "\n"
    path.write_text(head + yaml.safe_dump({"gics": out}, sort_keys=False, allow_unicode=True, width=140))
