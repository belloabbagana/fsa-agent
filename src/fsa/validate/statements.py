"""Arithmetic checks and the NGN Market reconciliation for an extracted annual-report statement.

Which lines add up to which total, and which provider metric maps to which line, are analyst
decisions held in rules/integrity/statement_identities.yaml and rules/provider_reconciliation.yaml.
This module only applies them. Code does all the arithmetic."""
import math
import re
from pathlib import Path

import yaml

from fsa.warehouse.db import ROOT

STATEMENT_TO_PROVIDER = {"profit_or_loss": "Income Statement", "financial_position": "Balance Sheet", "cash_flows": "Cash Flow"}


def _rows(con, ticker, fy, statement):
    return con.execute(
        "SELECT seq, section, label, entity, period_end, value, unit FROM pdf_statement_lines "
        "WHERE ticker=? AND fiscal_year=? AND statement=? ORDER BY seq", [ticker, fy, statement]).fetchall()


def _find(rows, spec, entity, period_end):
    """Resolve 'Label', 'Label@Section', '#n', or alternatives 'A|B' to one value.
    A leading '?' marks a part this filing may not print at all (counted as nothing, and said so)."""
    optional = spec.startswith("?")
    spec = spec.lstrip("?")
    last = None
    for alt in spec.split("|"):
        if alt.startswith("#"):
            n = int(alt[1:])
            hit = [r for r in rows if r[0] == n and r[3] == entity and r[4] == period_end]
        else:
            label, _, section = alt.partition("@")
            hit = [r for r in rows if r[2].lower() == label.lower() and r[3] == entity and r[4] == period_end
                   and (not section or r[1].lower() == section.lower())]
        if len(hit) == 1:
            return hit[0][5], hit[0][2] or f"#{hit[0][0]}"
        last = len(hit)
    if optional and last == 0:
        return 0.0, "(not printed in this filing)"
    raise LookupError(f"{spec!r}: found {last} lines")


def _expand(spec):
    m = re.fullmatch(r"#(\d+)\.\.#(\d+)", spec)
    return [f"#{i}" for i in range(int(m[1]), int(m[2]) + 1)] if m else [spec]


def run_identities(con, ticker, fy):
    rules = yaml.safe_load((ROOT / "rules" / "integrity" / "statement_identities.yaml").read_text())[ticker][f"FY{fy}"]
    con.execute("DELETE FROM integrity_checks WHERE ticker=? AND fiscal_year=? AND check_name NOT LIKE 'comparative:%'", [ticker, fy])
    results = []
    for statement, checks in rules.items():
        rows = _rows(con, ticker, fy, statement)
        combos = sorted({(r[3], r[4]) for r in rows}, key=lambda t: (t[0], t[1]), reverse=True)
        for entity, pend in combos:
            for c in checks:
                parts = [p for spec in c["parts"] for p in _expand(spec)]
                try:
                    total, _ = _find(rows, c["total"], entity, pend)
                    actual = sum(_find(rows, p, entity, pend)[0] for p in parts)
                    actual -= sum(_find(rows, p, entity, pend)[0] for p in c.get("minus", []))
                except LookupError as e:
                    results.append((statement, entity, pend, c["name"], None, None, f"fail: {e}"))
                    continue
                tol = max(1.0, math.ceil(len(parts) / 2))        # each printed line is rounded to N'million
                status = "pass" if abs(total - actual) <= tol else "fail"
                results.append((statement, entity, pend, c["name"], total, actual, status))
    for statement, entity, pend, name, total, actual, status in results:
        con.execute("INSERT INTO integrity_checks (ticker,fiscal_year,statement,entity,period_end,check_name,expected,actual,status) "
                    "VALUES (?,?,?,?,?,?,?,?,?)", [ticker, fy, statement, entity, pend, name, total, actual, status])
    return results


def run_reconciliation(con, ticker, fy):
    from datetime import date
    rules = yaml.safe_load((ROOT / "rules" / "provider_reconciliation.yaml").read_text())[ticker]
    pend = date(fy, 12, 31)
    con.execute("DELETE FROM reconciliation WHERE ticker=? AND fiscal_year=?", [ticker, fy])
    out = []
    for statement, items in rules.items():
        rows = _rows(con, ticker, fy, statement)
        for it in items:
            prov = con.execute("SELECT value FROM statement_lines_current WHERE ticker=? AND period_type='FY' AND fiscal_year=? AND metric=?",
                               [ticker, fy, it["metric"]]).fetchone()
            prov = prov[0] if prov else None
            if "derived" in it:
                out.append((it["metric"], "derived: " + it["derived"], None, prov, None, "not_comparable"))
                continue
            labels = it["pdf"] if isinstance(it["pdf"], list) else [it["pdf"]]
            try:
                vals = []
                for lab in labels:
                    spec = lab + (f"@{it['section']}" if "section" in it else "")
                    vals.append(_find(rows, spec, "group", pend)[0])
            except LookupError as e:
                out.append((it["metric"], " + ".join(labels), None, prov, None, f"not_comparable ({e})"))
                continue
            scale = it.get("scale", 1_000_000)
            pdf_value = sum(vals) * scale * it.get("sign", 1)
            if prov is None:
                out.append((it["metric"], " + ".join(labels), pdf_value, None, None, "not_comparable (provider value missing)"))
                continue
            diff = prov - pdf_value
            tol = 0.5 * scale if scale > 1 else 0.005
            status = "match" if abs(diff) <= tol else "differs"
            if status == "differs":
                # Does the provider match the comparative shown in the NEXT year's report? Then the company restated it.
                nxt = _rows(con, ticker, fy + 1, statement)
                try:
                    restated = sum(_find(nxt, (lab + (f"@{it['section']}" if "section" in it else "")), "group", pend)[0] for lab in labels)
                    restated *= scale * it.get("sign", 1)
                    if abs(prov - restated) <= tol:
                        status = "match_restated_comparative"
                except LookupError:
                    pass
            out.append((it["metric"], " + ".join(labels), pdf_value, prov, diff, status))
    for metric, label, pv, prv, diff, status in out:
        con.execute("INSERT INTO reconciliation (ticker,fiscal_year,provider_metric,pdf_label,pdf_value,provider_value,difference,status) "
                    "VALUES (?,?,?,?,?,?,?,?)", [ticker, fy, metric, label, pv, prv, diff, status])
    return out


def run_comparatives(con, ticker, fy):
    """Does this report's prior-year column equal the prior-year report's own figures?
    A difference can mean a restatement or reclassification: examine it, never assume which is right.
    Lines are matched on statement + printed label + which occurrence of that label it is."""
    from datetime import date
    prior_end = date(fy - 1, 12, 31)
    have_prior = con.execute("SELECT count(*) FROM pdf_statement_lines WHERE ticker=? AND fiscal_year=?", [ticker, fy - 1]).fetchone()[0]
    if not have_prior:
        return None

    def keyed(report_year):
        rows = con.execute(
            "SELECT statement, seq, label, entity, value FROM pdf_statement_lines "
            "WHERE ticker=? AND fiscal_year=? AND period_end=? AND label<>'' ORDER BY statement, seq", [ticker, report_year, prior_end]).fetchall()
        seen, out = {}, {}
        for st, seq, label, ent, val in rows:
            n = seen[(st, label.lower(), ent)] = seen.get((st, label.lower(), ent), 0) + 1
            out[(st, label.lower(), n, ent)] = (label, val)
        return out

    later, earlier = keyed(fy), keyed(fy - 1)
    con.execute("DELETE FROM integrity_checks WHERE ticker=? AND fiscal_year=? AND check_name LIKE 'comparative:%'", [ticker, fy])
    results = []
    for key, (label, comparative) in later.items():
        st, _, n, ent = key
        if key not in earlier:
            status, original = "unmatched", None
        else:
            original = earlier[key][1]
            status = "pass" if comparative is not None and original is not None and abs(comparative - original) < 0.5 else "fail"
        results.append((st, ent, label, original, comparative, status))
        con.execute("INSERT INTO integrity_checks (ticker,fiscal_year,statement,entity,period_end,check_name,expected,actual,status) VALUES (?,?,?,?,?,?,?,?,?)",
                    [ticker, fy, st, ent, prior_end, f"comparative:{label}", original, comparative, status])
    return results
