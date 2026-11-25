"""Dashboard data layer: the viewer export fails closed and carries no peer or provider content; answers are append-only; Ask answers are number-checked."""
import json

import pytest

from fsa.app import ask as askmod
from fsa.app import data as appdata

CFG = {"companies": [{"ticker": "CORE", "name": "Core Plc", "role": "core", "conflict_check": "pass"},
                     {"ticker": "PEERX", "name": "Peer Example Plc", "role": "peer", "conflict_check": "pass"}]}


def finding(fid, section="D", names=("a",), trigger="a > 1"):
    return {"id": fid, "section": section, "numbers": [{"name": n, "value": 1.0} for n in names], "trigger": trigger}


def report():
    return {"company": "Core Plc", "findings": [finding("D-01"), finding("K-01", "K"), finding("J-02", "J", ("roic", "roic_peer_median")), finding("X-1", "J", ("a",), "a > a_cmp")],
            "data_notes": [{"category": "standard", "severity": "standard", "text": "Audited figures."},
                           {"category": "third_party", "severity": "note", "text": "Cross-check."},
                           {"category": "standard", "severity": "standard", "text": "Third-party market data (NGN Market) is used for peers."},
                           {"category": "peers", "severity": "note", "text": "Peer set small."}],
            "ratings": {"data_quality": {"grade": "Moderate", "why": "a peer lacks a conflict check", "inputs": {"peer_set_unchecked": 1}}}, "inputs_missing": []}


def test_sanitize_drops_peer_findings_notes_and_internal_inputs():
    out = appdata.sanitize(report(), CFG)
    assert [f["id"] for f in out["findings"]] == ["D-01"]
    assert [n["text"] for n in out["data_notes"]] == ["Audited figures."]
    assert out["ratings"]["data_quality"]["inputs"] == {} and "peer" not in json.dumps(out["ratings"]).lower()
    assert "inputs_missing" not in out


def test_forbidden_words_include_peer_tickers_names_and_the_provider():
    bad = appdata.forbidden_words(CFG)
    assert "peerx" in bad and "peer example" in bad and "ngn market" in bad and "core" not in bad


def test_answers_are_append_only_with_a_version_log(tmp_path, monkeypatch):
    monkeypatch.setattr(appdata, "ANSWERS", tmp_path)
    a = appdata.save_answer("T", 2025, "D-01", "Receivables were collected in January.", "management", "call 3 Dec")
    b = appdata.save_answer("T", 2025, "D-01", "Corrected: collected in February.", "management")
    log = appdata.load_answers("T", 2025)
    assert [x["version"] for x in log] == ["0.1-draft+answer1", "0.1-draft+answer2"] and a["answer"] != b["answer"] and len(log) == 2
    with pytest.raises(ValueError):
        appdata.save_answer("T", 2025, "D-01", "   ", "management")
    with pytest.raises(ValueError):
        appdata.save_answer("T", 2025, "D-01", "text", "a stranger")


def test_ask_check_rejects_unsupported_numbers_and_banned_words():
    text, q = "Revenue was 4,306,704 in FY2025. [Section B]", "What was revenue?"
    assert askmod.check_answer(text, "Revenue was 4,306,704 [Section B].", q) == []
    assert any("numbers not found" in p for p in askmod.check_answer(text, "Revenue was 4,500,000.", q))
    assert any("banned" in p for p in askmod.check_answer(text, "You should buy.", q))
    assert any("underscore" in p for p in askmod.check_answer(text, "The net_margin rose.", q))
    assert askmod.check_answer(text, "That is not in this report.", q) == []


def test_advice_questions_get_the_fixed_reply_without_a_model_call():
    for q in ("Should I buy this stock?", "Is it a good investment?", "What is the price target?"):
        r = askmod.answer_question({}, q)
        assert r["status"] == "answered" and r["cost_usd"] == 0.0 and "do not give" in r["answer"]
    assert not askmod.ADVICE.search("What drove the fall in ROE in FY2025?")
