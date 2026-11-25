"""Five-stage DuPont analysis of ROE, and an exact split of each year's change in ROE between the stages.

ROE = tax burden x interest burden x operating margin x asset turnover x equity multiplier   (x owners' share of profit, shown separately)
  tax burden        profit for the year / profit before tax          (how much of pre-tax profit the tax charge leaves)
  interest burden   profit before tax / operating profit             (how much of operating profit survives finance costs and items below it)
  operating margin  operating profit / revenue
  asset turnover    revenue / average total assets
  financial leverage average total assets / average owners' equity (also called the equity multiplier)
  owners' share     profit attributable to owners / profit for the year (1.00 when there is no minority interest; keeps the identity exact)

Code does all arithmetic. The change in ROE between two years is split with the log method: each stage gets
(change in ln stage / change in ln ROE) x change in ROE, so the pieces add up to the change exactly and do not depend on the order of the stages.
A model may describe the figures afterwards, but only in words checked by code (every number must be one of the supplied figures).
"""
import json
import math
import re

import yaml

from fsa.findings import writer as fwr
from fsa.findings.triggers import build_context
from fsa.map.model_proposer import ask, verify_model
from fsa.warehouse.db import ROOT

STAGES = [("tax_burden", "Tax burden", "x"), ("interest_burden", "Interest burden", "x"), ("operating_margin", "Operating margin", "pct"),
          ("asset_turnover", "Asset turnover", "x"), ("equity_multiplier", "Financial leverage", "x")]
OWNERS = ("owners_share", "Owners' share of profit", "x")
CONTEXT = ["core_operating_margin", "effective_tax_rate", "interest_cover", "debt_to_equity", "revenue_growth", "net_profit_growth",
           "translation_reserve_share_of_equity", "fx_share_of_operating_profit", "biological_fv_share_of_operating_profit", "monetary_position_share_of_pretax_profit"]
ACCOUNTS = ["PL_REVENUE", "PL_OPERATING_PROFIT", "BS_TOTAL_ASSETS", "BS_CASH", "BS_EQUITY_OWNERS", "DEBT_GROSS"]


def audited_year(con, chart, ratio_rules, ticker: str, fy: int) -> dict | None:
    rows = {r[0]: r[1] for r in con.execute("SELECT ratio_id, value FROM ratios WHERE ticker=? AND fiscal_year=? AND status='ok'", [ticker, fy]).fetchall()}
    need = ["roe"] + [s[0] for s in STAGES]
    if any(k not in rows for k in need):
        return None
    try:
        cur, prior, *_ = build_context(con, chart, ratio_rules, ticker, fy)
    except SystemExit:
        return None
    owners = cur["PL_ATTRIB_OWNERS"] / cur["PL_PROFIT_FOR_YEAR"] if cur.get("PL_PROFIT_FOR_YEAR") else None
    if owners is None:
        return None
    legs = {k: rows[k] for k, *_ in STAGES} | {"owners_share": owners}
    return finish(fy, rows["roe"], legs) | {
        "context": {k: rows[k] for k in CONTEXT if k in rows},
        "accounts": {k: cur.get(k) for k in ACCOUNTS if k in cur} | {f"prior_{k}": (prior or {}).get(k) for k in ("BS_TOTAL_ASSETS", "BS_CASH") if prior}}


def finish(fy: int, roe: float, legs: dict) -> dict:
    prod = math.prod(v / 100 if k == "operating_margin" else v for k, v in legs.items()) * 100
    before_leverage = roe / legs["equity_multiplier"]
    return {"fiscal_year": fy, "roe": roe, "legs": legs, "product_of_legs": prod, "identity_gap_pp": prod - roe,
            "roe_before_leverage": before_leverage, "leverage_adds_pp": roe - before_leverage}


def decompose(prev: dict, cur: dict) -> dict | None:
    """Exact log split of cur.roe - prev.roe over the stages (and owners' share). None when a stage is not positive."""
    keys = [s[0] for s in STAGES] + ["owners_share"]
    if prev["roe"] <= 0 or cur["roe"] <= 0 or any(prev["legs"][k] <= 0 or cur["legs"][k] <= 0 for k in keys):
        return {"fiscal_year": cur["fiscal_year"], "status": "N/M", "reason": "ROE or a stage is zero or negative, so the log split is not defined"}
    d_roe, d_ln = cur["roe"] - prev["roe"], math.log(cur["roe"] / prev["roe"])
    ln = {k: math.log(cur["legs"][k] / prev["legs"][k]) for k in keys}
    if abs(d_ln) < 1e-12:
        return {"fiscal_year": cur["fiscal_year"], "status": "ok", "delta_roe_pp": d_roe, "contributions_pp": {k: 0.0 for k in keys}, "residual_pp": 0.0}
    contrib = {k: ln[k] / d_ln * d_roe for k in keys}
    resid = d_roe - sum(contrib.values())
    ranked = sorted(keys, key=lambda k: -abs(contrib[k]))
    return {"fiscal_year": cur["fiscal_year"], "status": "ok", "delta_roe_pp": d_roe, "contributions_pp": contrib, "residual_pp": resid, "ranked": ranked}


def build_company(con, chart, ratio_rules, ticker: str, fy: int, years: int = 3) -> dict:
    ys = [audited_year(con, chart, ratio_rules, ticker, y) for y in range(fy - years + 1, fy + 1)]
    ys = [y for y in ys if y]
    changes = [decompose(a, b) for a, b in zip(ys, ys[1:]) if b["fiscal_year"] == a["fiscal_year"] + 1]
    return {"ticker": ticker, "fiscal_year": fy, "basis": "audited statements, Group; ROE on average owners' equity; nominal naira",
            "years": ys, "changes": changes}


# ---------------------------------------------------------------- peers (provider basis, internal only)
def provider_year(con, ticker: str, fy: int) -> dict | None:
    r = {k: v for k, v in con.execute("SELECT ratio_id, value FROM comparison_ratios WHERE ticker=? AND fiscal_year=? AND status='ok'", [ticker, fy]).fetchall()}
    need = ("net_margin", "pretax_margin", "operating_margin", "asset_turnover", "roe", "roa")
    if any(r.get(k) in (None, 0) for k in need):
        return None
    legs = {"tax_burden": r["net_margin"] / r["pretax_margin"], "interest_burden": r["pretax_margin"] / r["operating_margin"],
            "operating_margin": r["operating_margin"], "asset_turnover": r["asset_turnover"], "equity_multiplier": r["roe"] / r["roa"], "owners_share": 1.0}
    return finish(fy, r["roe"], legs)


def peer_table(con, company: dict, cfg: dict, fy: int) -> dict:
    group = company["industry_peer_set"]
    names = [c["ticker"] for c in cfg["companies"] if c.get("industry_peer_set") == group]
    out = {}
    for t in names:
        prev, cur = provider_year(con, t, fy - 1), provider_year(con, t, fy)
        out[t] = {"year": cur, "change": decompose(prev, cur) if prev and cur else None,
                  "conflict_check": next(c.get("conflict_check") for c in cfg["companies"] if c["ticker"] == t)}
    return {"group": group, "fiscal_year": fy, "basis": "NGN Market data for every company (ROE on total equity); internal draft", "companies": out}


# ---------------------------------------------------------------- the words (model describes; code checks)
SYSTEM = (
    "You explain what drives a company's return on equity (ROE) to a CFA-trained analyst, using only the five-stage DuPont figures supplied. Rules:\n"
    "1. Use ONLY figures given, copied exactly as shown in 'show'. Never calculate, round differently or state a number that is not listed. Amounts are N millions.\n"
    "2. Neutral wording. These are items to examine, never accusations. No buy, sell, hold or recommendation. Do not imply fraud, manipulation or wrongdoing.\n"
    "3. The 'contributions' split the change in ROE (percentage points) between stages and add up to the change. Name the stages by plain words: tax burden, interest burden, operating margin, asset turnover, financial leverage. Never use identifiers or underscores.\n"
    "4. Say what the figures show, not why it happened. A cause may only be offered as something to examine. Do not use 'because', 'due to' or 'driven by' for anything other than the supplied contribution figures.\n"
    "5. Where core_operating_margin is below operating_margin, say the operating margin includes gains outside core trading. Where translation_reserve_share_of_equity is high, say equity includes a translation reserve. Use only the figures supplied.\n"
    "6. No judgement words the figures do not establish (large, sharp, heavy, strong, weak, concerning, improvement).\n"
    "7. Do not describe leverage as good or bad. Call equity_multiplier 'financial leverage' (average total assets divided by average owners' equity); 'leverage adds' is ROE minus ROE before leverage.\n"
    "Reply with JSON only: {\"summary\": \"3 to 5 sentences on what drives ROE across the years\", \"years\": {\"<year>\": \"2 to 3 sentences for that year\"}, \"to_examine\": [\"question 1\", \"question 2\"]}. Include a 'years' entry only for years that have figures.")


def figures_for_prompt(data: dict) -> dict:
    sh = fwr.show
    years = {}
    for y in data["years"]:
        years[str(y["fiscal_year"])] = {
            "roe_pct": sh(y["roe"]), "stages": {k: sh(v) for k, v in y["legs"].items()}, "roe_before_leverage_pct": sh(y["roe_before_leverage"]),
            "leverage_adds_pp": sh(y["leverage_adds_pp"]), "context": {k: sh(v) for k, v in y["context"].items()},
            "accounts_N_millions": {k: sh(v) for k, v in y["accounts"].items() if v is not None}}
    changes = {str(c["fiscal_year"]): {"change_in_roe_pp": sh(c["delta_roe_pp"]), "contributions_pp": {k: sh(v) for k, v in c["contributions_pp"].items()}}
               for c in data["changes"] if c["status"] == "ok"}
    return {"company": data["ticker"], "latest_year": data["fiscal_year"], "units": "stages: tax/interest burden, turnover and multiplier in times (x); operating margin and ROE in %", "years": years, "changes": changes}


def _numbers_ok(data: dict) -> set:
    rec = {"fiscal_year": data["fiscal_year"], "facts": [], "trigger": "", "title": "", "question": ""}
    vals = []
    for y in data["years"]:
        vals += [y["roe"], y["roe_before_leverage"], y["leverage_adds_pp"], *y["legs"].values(), *y["context"].values(), *[v for v in y["accounts"].values() if v is not None]]
    for c in data["changes"]:
        if c["status"] == "ok":
            vals += [c["delta_roe_pp"], *c["contributions_pp"].values()]
    rec["facts"] = [{"value": v} for v in vals]
    ok = fwr.allowed_numbers(rec)
    ok |= {str(y["fiscal_year"]) for y in data["years"]} | {str(y["fiscal_year"] - 1) for y in data["years"]} | {"1", "2", "3", "4", "5"}   # 'five-stage', counts of years
    return ok


def check_commentary(data: dict, got: dict) -> list[str]:
    texts = [got.get("summary", "")] + list((got.get("years") or {}).values()) + list(got.get("to_examine") or [])
    blob = " ".join(str(t) for t in texts)
    problems = []
    bad = {n for n in fwr.NUMBER.findall(blob) if n.replace(",", "") not in _numbers_ok(data)}
    if bad:
        problems.append("numbers not in the supplied figures: " + ", ".join(sorted(bad)))
    if fwr.BANNED.search(blob):
        problems.append("banned wording: " + fwr.BANNED.search(blob).group(0))
    if re.search(r"[a-z]+_[a-z]+", blob):
        problems.append("identifier with an underscore used instead of plain words")
    if not got.get("summary") or not got.get("years"):
        problems.append("summary or years missing")
    return problems


def explain(data: dict, model: str) -> dict:
    base = "Explain what drives ROE for this company from these figures.\n" + json.dumps(figures_for_prompt(data), indent=1)
    prompt, used, cost, problems = base, set(), 0.0, []
    for attempt in (1, 2):
        ans = ask(prompt, model, system=SYSTEM)
        if ans.is_error:
            raise RuntimeError(f"the model call failed: {ans.error_text}")
        used |= verify_model(ans, model)
        cost += ans.cost_usd or 0.0
        m = re.search(r"\{.*\}", ans.text, re.S)
        try:
            got = json.loads(m.group(0)) if m else None
        except json.JSONDecodeError:
            got = None
        problems = ["reply was not the JSON asked for"] if got is None else check_commentary(data, got)
        if not problems:
            return {"status": "written", "summary": got["summary"], "years": got["years"], "to_examine": got.get("to_examine", []), "written_by": sorted(used), "attempts": attempt, "cost_usd": cost}
        prompt = base + "\n\nYour previous answer was rejected by code for these reasons; fix them and answer again:\n- " + "\n- ".join(problems)
    return {"status": "needs a person", "problems": problems, "written_by": sorted(used), "attempts": 2, "cost_usd": cost}


# ---------------------------------------------------------------- markdown
LABEL = {k: n for k, n, _ in STAGES} | {"owners_share": "Owners' share of profit"}


def _f(k: str, v: float) -> str:
    return f"{v:,.1f}%" if k in ("operating_margin", "roe") else f"{v:,.2f}x"


def to_markdown(data: dict, words: dict | None = None) -> list[str]:
    ys = data["years"]
    L = ["| Stage | " + " | ".join(f"FY{y['fiscal_year']}" for y in ys) + " |", "|---|" + "---:|" * len(ys)]
    for k, name, _ in STAGES + [OWNERS]:
        L.append(f"| {name} | " + " | ".join(_f(k, y["legs"][k]) for y in ys) + " |")
    L += ["| **ROE (average owners' equity)** | " + " | ".join(f"**{y['roe']:,.1f}%**" for y in ys) + " |",
          "| ROE before leverage (ROE / financial leverage) | " + " | ".join(f"{y['roe_before_leverage']:,.1f}%" for y in ys) + " |",
          "| Leverage adds (percentage points) | " + " | ".join(f"{y['leverage_adds_pp']:,.1f}" for y in ys) + " |", "",
          "*The five stages and the owners' share multiply to ROE. Identity gap (percentage points): " + ", ".join(f"FY{y['fiscal_year']} {y['identity_gap_pp']:.4f}" for y in ys) + ".*", ""]
    ok = [c for c in data["changes"] if c["status"] == "ok"]
    if ok:
        L += ["**What moved ROE each year** (percentage points; the pieces add up to the change)", "",
              "| Stage | " + " | ".join(f"FY{c['fiscal_year'] - 1} to FY{c['fiscal_year']}" for c in ok) + " |", "|---|" + "---:|" * len(ok)]
        for k in [s[0] for s in STAGES] + ["owners_share"]:
            L.append(f"| {LABEL[k]} | " + " | ".join(f"{c['contributions_pp'][k]:+,.1f}" for c in ok) + " |")
        L += ["| **Change in ROE** | " + " | ".join(f"**{c['delta_roe_pp']:+,.1f}**" for c in ok) + " |", ""]
    for c in data["changes"]:
        if c["status"] != "ok":
            L.append(f"*FY{c['fiscal_year']}: split not available ({c['reason']}).*")
    if words and words["status"] == "written":
        L += ["**What is driving ROE**", "", words["summary"], ""] + [f"- FY{y}: {t}" for y, t in sorted(words["years"].items())]
        if words.get("to_examine"):
            L += ["", "**To examine**", ""] + [f"- {q}" for q in words["to_examine"]]
        L += ["", f"*Wording drafted by {', '.join(words['written_by'])}; every number checked by code against the figures above.*", ""]
    elif words:
        L += ["*The drafted explanation failed the code checks and is not shown.*", ""]
    return L


def peers_markdown(t: dict) -> str:
    L = [f"# DuPont, {t['group']} peers, FY{t['fiscal_year']} (INTERNAL DRAFT)", "",
         f"> {t['basis']}. Peer names stay internal until each has a conflict check recorded as pass (project rule 3). Peers have no audited extraction here, so ROE is on total equity and there is no owners' share.", "",
         "| Company | Check | Tax burden | Interest burden | Operating margin | Asset turnover | Financial leverage | ROE | Biggest mover of the change in ROE |", "|---|---|---:|---:|---:|---:|---:|---:|---|"]
    for tk, d in t["companies"].items():
        y, c = d["year"], d["change"]
        if not y:
            L.append(f"| {tk} | {d['conflict_check']} | n/a | n/a | n/a | n/a | n/a | n/a | data not available |")
            continue
        mover = (f"{LABEL[c['ranked'][0]]} ({c['contributions_pp'][c['ranked'][0]]:+.1f} of {c['delta_roe_pp']:+.1f} pp)" if c and c["status"] == "ok" else "n/a")
        L.append(f"| {tk} | {d['conflict_check']} | " + " | ".join(_f(k, y["legs"][k]) for k, *_ in STAGES) + f" | {y['roe']:,.1f}% | {mover} |")
    return "\n".join(L) + "\n"
