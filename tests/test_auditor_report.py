"""Auditor's report reader: classifies by heading and phrase, tolerates lost ligatures, and a quote must be found again in the text."""
from fsa.extract import auditor_report as aud
from fsa.findings.triggers import text_rule

CLEAN = {1: "Independent Auditor's Report\nOpinion\nIn our opinion, the financial statements give a true and fair view of the financial position.\n"
            "Basis for opinion\nWe conducted our audit in accordance with ISAs.\nKey audit matters\n1. Valuation of inventory\nWe focused on stock.\n"
            "Other matter\nThe prior year was audited by another auditor.\nSigned for KPMG Professional Services\nChartered Accountants"}
DAMAGED = {1: "Opinion\nIn our opinion, the statements give a true and fair view of the financial posi\x00on.\nBasis for opinion\nWe conducted our audit.\n"
              "Key Audit Ma\x00ers\nBiological assets\nMaterial uncertainty related to going concern\nWe draw attention to going concern doubts."}
QUALIFIED = {1: "Qualified Opinion\nIn our opinion, except for the effects of the matter described below, the statements give a true and fair view.\n"
                "Basis for Qualified Opinion\nWe could not verify inventory."}


def test_unmodified_opinion_with_sections():
    r = aud.analyse(CLEAN)
    assert r["opinion"]["type"] == "unmodified" and r["firm"] == "KPMG" and r["signature_found"]
    assert r["key_audit_matters"]["section_present"] and len(r["other_matter"]) == 1 and not r["going_concern"]["material_uncertainty_section"]


def test_lost_ligatures_do_not_hide_headings_or_phrases():
    r = aud.analyse(DAMAGED)
    assert r["damaged_ligatures"] and r["key_audit_matters"]["section_present"] and r["going_concern"]["material_uncertainty_section"]
    assert r["opinion"]["type"] == "unmodified"


def test_qualified_opinion_is_detected_with_a_quote():
    r = aud.analyse(QUALIFIED)
    assert r["opinion"]["type"] == "qualified" and "except for" in r["opinion"]["quote"].lower()


def test_boilerplate_in_auditor_responsibilities_is_not_a_modification():
    pages = {1: "Opinion\nIn our opinion, the statements give a true and fair view.\nBasis for opinion\nWe audited.\n"
                "Auditor's responsibilities\nWe do not express an opinion on the effectiveness of internal control. If a material uncertainty exists we draw attention."}
    r = aud.analyse(pages)
    assert r["opinion"]["type"] == "unmodified" and not r["going_concern"]["material_uncertainty_section"]


def test_quote_must_exist_in_the_text():
    assert aud.verify_quote("Valuation of inventory", CLEAN)
    assert aud.verify_quote("financial posi on", DAMAGED) or aud.verify_quote("the financial position", DAMAGED)
    assert not aud.verify_quote("Valuation of receivables", CLEAN)
    assert not aud.verify_quote("abc", CLEAN)                      # too short to prove anything


def test_red_flag_input_and_text_rules():
    ok, bad = aud.analyse(CLEAN), aud.analyse(DAMAGED)
    assert aud.red_flags(ok) == 0.0 and aud.red_flags(bad) == 1.0 and aud.red_flags(None) is None
    a01 = {"id": "A-01"}
    assert text_rule(a01, ok)["state"] == "not fired"
    q = text_rule(a01, aud.analyse(QUALIFIED))
    assert q["state"] == "fired" and q["evidence"][0]["quote"]
    assert "not been read" in text_rule(a01, None)["reason"]
    assert "notes" in text_rule({"id": "A-05"}, ok)["reason"]
