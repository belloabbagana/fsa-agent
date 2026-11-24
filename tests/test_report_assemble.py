import json

import jsonschema

from fsa.analyse.ratios import NotMeaningful
from fsa.report import assemble as asm
from fsa.warehouse.db import ROOT

FINDING = {"id": "X-01", "section": "D", "title": "t", "type": "risk", "question": "q?", "why": "w", "hypotheses": [{"h": "h", "confirm": "c", "rule_out": "r"}],
           "who_answers": "management", "docs_needed": [], "materiality": "high", "trigger": "a > 1", "status": "written", "disclaimer": "d",
           "observation": "obs", "why_it_matters": "wim", "facts": [{"name": "a", "value": 2.0, "unit": "x", "source": [{"statement": "s", "label": "l", "page": 3}]}],
           "review": {"verdict": "supported", "reviewed_by": ["m"], "text_checks": "passed", "problems": [], "overlooked_explanations": [], "context_for_analyst": ""}}


def report(findings):
    return {"schema": "report.v1", "version": "0.1-draft", "status": "draft", "generated_on": "2026-11-24", "ticker": "T", "company": "T plc", "fiscal_year": 2025,
            "amounts_unit": "N millions", "disclaimer": "Educational analysis, not investment advice.", "not_generated": asm.NOT_GENERATED, "scorecards": {}, "findings": findings,
            "data_notes": [{"category": "standard", "severity": "standard", "text": "n"}], "inputs_missing": [], "section_titles": asm.load_layout()["section_titles"]}


def test_format_units():
    assert asm.fmt(1234.4, "m") == "1,234" and asm.fmt(12.34, "pct") == "12.3%" and asm.fmt(None, "m") == "n/a" and asm.fmt(0.0, "x") == "0.00x"


def test_cell_not_meaningful_is_not_zero():
    class Ev:
        def ev(self, node):
            raise NotMeaningful("no base")
    v, note = asm._cell(Ev(), "roe")
    assert v is None and note.startswith("N/M")


def test_finding_record_keeps_sources_and_open_status():
    r = asm.finding_record(FINDING)
    assert r["status"] == "open" and r["numbers"][0]["sources"][0]["page"] == 3 and r["review"]["verdict"] == "supported"


def test_failed_wording_has_no_observation():
    r = asm.finding_record(FINDING | {"status": "needs a person"})
    assert r["observation"] is None and r["wording_status"] == "needs a person"


def test_report_validates_and_markdown_has_all_sections():
    rep = report([asm.finding_record(FINDING)])
    jsonschema.validate(rep, json.loads((ROOT / "schemas" / "report.v1.json").read_text()))
    md = asm.to_markdown(rep)
    for h in ("## A.", "## B.", "## K.", "## L.", "## M.", "## Data notes and disclaimers", "Not generated yet"):
        assert h in md
    assert "No buy, sell or hold" in md or "not investment advice" in md


def test_schema_rejects_bad_materiality():
    bad = asm.finding_record(FINDING) | {"materiality": "huge"}
    try:
        jsonschema.validate(report([bad]), json.loads((ROOT / "schemas" / "report.v1.json").read_text()))
    except jsonschema.ValidationError:
        return
    raise AssertionError("schema accepted an invalid materiality")
