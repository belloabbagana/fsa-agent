"""Data layer for the dashboard: load reports, save analyst answers, and export a safe snapshot for the viewer.

Analyst mode reads the full local reports. Viewer mode reads only app/snapshot/, which `export_viewer` builds for the two core companies from their
audited annual reports. The export drops everything that came from or names peers or the NGN Market data, and FAILS CLOSED: if any forbidden
word is still in a snapshot file, nothing is written (project rules 2 and 3).
"""
import json
import re
from datetime import datetime, timezone

import yaml

from fsa.warehouse.db import ROOT

REPORTS = ROOT / "reports"
SNAPSHOT = ROOT / "app" / "snapshot"
ANSWERS = ROOT / "data" / "answers"
INTERIM = ROOT / "data" / "interim"
DROP_NOTE_CATEGORIES = {"peers", "third_party"}
PEER_FACT = re.compile(r"_(peer_|cmp)")
ANSWER_WHO = ["management", "public sources", "field check"]


def companies_cfg() -> dict:
    return yaml.safe_load((ROOT / "config" / "companies.yaml").read_text())


def analyst_available() -> bool:
    return REPORTS.exists() and any(REPORTS.glob("*/FY*/report.json"))


def list_reports(mode: str) -> list[tuple[str, int]]:
    base = REPORTS if mode == "analyst" else SNAPSHOT
    found = []
    for p in sorted(base.glob("*/FY*/report.json") if mode == "analyst" else base.glob("*_FY*.json")):
        t, fy = (p.parent.parent.name, int(p.parent.name[2:])) if mode == "analyst" else (p.stem.split("_FY")[0], int(p.stem.split("_FY")[1]))
        found.append((t, fy))
    return found


def load_report(ticker: str, fy: int, mode: str) -> dict:
    path = REPORTS / ticker / f"FY{fy}" / "report.json" if mode == "analyst" else SNAPSHOT / f"{ticker}_FY{fy}.json"
    return json.loads(path.read_text())


def internal_markdown(ticker: str, fy: int, name: str) -> str | None:
    p = INTERIM / ticker / f"FY{fy}" / name
    return p.read_text() if p.exists() else None


# ---------------------------------------------------------------- analyst answers (a finding's question answered by a person)
def answers_path(ticker: str, fy: int):
    return ANSWERS / ticker / f"FY{fy}.jsonl"


def load_answers(ticker: str, fy: int) -> list[dict]:
    p = answers_path(ticker, fy)
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()] if p.exists() else []


def save_answer(ticker: str, fy: int, finding_id: str, text: str, who: str, source_note: str = "") -> dict:
    """Append an answer. History is never overwritten: each answer is a new line, and the finding's version counter rises by one."""
    if who not in ANSWER_WHO:
        raise ValueError(f"who must be one of {ANSWER_WHO}")
    if not text.strip():
        raise ValueError("an empty answer is not saved")
    prev = [a for a in load_answers(ticker, fy) if a["finding_id"] == finding_id]
    rec = {"finding_id": finding_id, "answer": text.strip(), "answered_by": who, "source_note": source_note.strip(), "version": f"0.1-draft+answer{len(prev) + 1}",
           "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    p = answers_path(ticker, fy)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec


# ---------------------------------------------------------------- viewer export
def forbidden_words(cfg: dict) -> list[str]:
    words = ["ngn market", "ngnmarket", "ngn_market"]
    for c in cfg["companies"]:
        if c.get("role") == "peer":
            words += [c["ticker"].lower(), c["name"].lower().replace(" plc", "")]
    return words


def sanitize(rep: dict, cfg: dict) -> dict:
    out = {k: v for k, v in rep.items() if k not in ("inputs_missing",)}
    keep = []
    for f in rep["findings"]:
        if f["section"] == "K" or any(PEER_FACT.search(n["name"]) for n in f["numbers"]) or PEER_FACT.search(f.get("trigger") or ""):
            continue
        keep.append(f)
    out["findings"] = keep
    bad = forbidden_words(cfg)
    out["data_notes"] = [n for n in rep["data_notes"] if n["category"] not in DROP_NOTE_CATEGORIES and not any(w in n["text"].lower() for w in bad)]
    if rep.get("ratings"):
        r = json.loads(json.dumps(rep["ratings"]))
        r["data_quality"] = {"grade": r["data_quality"]["grade"], "why": "Based on mapping sign-off, the filing's own arithmetic, restatements and source cross-checks."}
        out["ratings"] = r
    out["viewer_note"] = "Public snapshot built from the company's audited annual report. Educational analysis, not investment advice."
    return out


def sources_for(ticker: str, fy: int) -> list[dict]:
    p = ROOT / "data" / "raw" / ticker / f"FY{fy}" / "source.json"
    if not p.exists():
        return []
    s = json.loads(p.read_text())
    return [{k: s.get(k) for k in ("description", "url", "downloaded", "sha256", "file") if s.get(k)}]


def export_viewer() -> list[str]:
    cfg = companies_cfg()
    bad = forbidden_words(cfg)
    core = [c["ticker"] for c in cfg["companies"] if c.get("role") == "core" and c.get("conflict_check") == "pass"]
    written, staged = [], {}
    for t, fy in list_reports("analyst"):
        if t not in core:
            continue
        rep = sanitize(load_report(t, fy, "analyst"), cfg)
        rep["sources"] = sources_for(t, fy)
        text = json.dumps(rep, indent=1, ensure_ascii=False, default=str)
        hit = [w for w in bad if w in text.lower()]
        if hit:
            raise SystemExit(f"Export stopped: {t} FY{fy} still contains forbidden word(s) {hit}. Nothing was written.")
        staged[f"{t}_FY{fy}.json"] = text
    SNAPSHOT.mkdir(parents=True, exist_ok=True)
    for old in SNAPSHOT.glob("*.json"):
        old.unlink()
    for name, text in staged.items():
        (SNAPSHOT / name).write_text(text)
        written.append(name)
    return written
