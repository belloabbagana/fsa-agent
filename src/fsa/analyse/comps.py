"""Industry comparison: ratios on one common data basis, industry statistics, and each core company's position vs its peers.

Position words say where a number sits, never whether that is good or bad. Counts are always kept, and a small peer
set is flagged, because a median of two companies is only indicative.
"""
import statistics
from datetime import date

import yaml

from fsa.analyse.forensic import load_rules as load_forensic, merged
from fsa.analyse.ratios import FACTOR, run_ratios
from fsa.warehouse.db import ROOT

MAP_PATH = ROOT / "rules" / "provider_to_chart.yaml"
CMP_PATH = ROOT / "rules" / "comparison.yaml"


def load(path):
    return yaml.safe_load(path.read_text())


def provider_values(con, mapping: dict, ticker: str, fy: int) -> dict | None:
    """chart code -> N millions, from the provider's annual data. Only lines the provider gave; assumed nils are added from the rules."""
    rows = dict(con.execute("SELECT metric, value FROM statement_lines_current WHERE ticker=? AND period_type='FY' AND fiscal_year=? AND value IS NOT NULL",
                            [ticker, fy]).fetchall())
    if not rows:
        return None
    out = {}
    for code, spec in mapping["map"].items():
        v = rows.get(spec["metric"])
        if v is not None:
            out[code] = v * spec.get("sign", 1) / 1e6
    for code in mapping.get("assume_zero", {}):
        out[code] = 0.0
    return out


def quartiles(values: list[float]):
    if len(values) < 2:
        return None, (values[0] if values else None), None
    q = statistics.quantiles(values, n=4, method="inclusive")
    return q[0], q[1], q[2]


def position(value: float, peers: list[float], cfg: dict) -> tuple[str, str, bool]:
    """-> (above|near|below|no peers, method, small_set). Says where the number sits, nothing about good or bad."""
    if not peers:
        return "no peers", "none", True
    small = len(peers) < cfg["min_n_for_quartiles"]
    if not small:
        q1, _, q3 = quartiles(peers)
        return ("below" if value < q1 else "above" if value > q3 else "near"), "quartiles of peers", False
    med = statistics.median(peers)
    band = abs(med) * cfg["near_band_pct"] / 100 if abs(med) > cfg["abs_band_when_median_near_zero"] else cfg["abs_band_when_median_near_zero"]
    pos = "near" if abs(value - med) <= band else ("above" if value > med else "below")
    return pos, ("peer median +/-%g%%" % cfg["near_band_pct"]) + (" (one peer only: indicative)" if len(peers) == 1 else ""), True


def compute_all(con, chart: dict, ratio_rules: dict) -> dict:
    mapping, cmp = load(MAP_PATH), load(CMP_PATH)
    ratio_rules = merged(ratio_rules, load_forensic())          # the forensic indices and flags are compared with peers too
    con.execute("DELETE FROM comparison_ratios")
    tickers = [r[0] for r in con.execute("SELECT DISTINCT ticker FROM statement_lines_current").fetchall()]
    n = 0
    for t in tickers:
        for fy in cmp["years"]:
            cur = provider_values(con, mapping, t, fy)
            if cur is None:
                continue
            prior = provider_values(con, mapping, t, fy - 1)
            for r in run_ratios(ratio_rules, chart, cur, prior, strict=True, overrides=cmp["overrides"]):
                con.execute("INSERT INTO comparison_ratios VALUES (?,?,?,?,?,?,?)", [t, fy, r["ratio_id"], r["unit"], r["value"], r["status"], r["reason"]])
                n += 1
    return {"rows": n, "companies": len(tickers)}


def stats_and_positions(con) -> dict:
    cmp = load(CMP_PATH)
    con.execute("DELETE FROM industry_stats"); con.execute("DELETE FROM peer_position")
    members = {}
    for t, ind, role in con.execute("SELECT c.ticker, c.gics_industry, c.role FROM companies c WHERE c.gics_industry IS NOT NULL").fetchall():
        members.setdefault(ind, []).append((t, role))
    rows = con.execute("SELECT ticker, fiscal_year, ratio_id, value, status FROM comparison_ratios").fetchall()
    val = {(t, fy, r): (v if s == "ok" else None) for t, fy, r, v, s in rows}
    keys = sorted({(fy, r) for _, fy, r, _, _ in rows})
    for ind, mem in members.items():
        for fy, r in keys:
            vs = [val[(t, fy, r)] for t, _ in mem if val.get((t, fy, r)) is not None]
            if not any((t, fy, r) in val for t, _ in mem):
                continue
            q1, med, q3 = quartiles(vs)
            con.execute("INSERT INTO industry_stats VALUES (?,?,?,?,?,?,?,?,?,?)",
                        [ind, fy, r, len(vs), len(mem), med, q1, q3, min(vs) if vs else None, max(vs) if vs else None])
            for t, role in mem:
                if role != "core" or (t, fy, r) not in val:
                    continue
                v = val[(t, fy, r)]
                if v is None:
                    con.execute("INSERT INTO peer_position VALUES (?,?,?,?,?,?,?,?,?,?,?)", [t, fy, r, None, None, None, None, None, "not meaningful", None, None])
                    continue
                peers = [val[(p, fy, r)] for p, _ in mem if p != t and val.get((p, fy, r)) is not None]
                pos, method, small = position(v, peers, cmp["position"])
                con.execute("INSERT INTO peer_position VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            [t, fy, r, v, len(peers), statistics.median(peers) if peers else None, min(peers) if peers else None, max(peers) if peers else None, pos, method, small])
    return {"industries": len(members)}
