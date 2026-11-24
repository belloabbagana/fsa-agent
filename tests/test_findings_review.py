"""Reviewer guards: the reply must be well-formed, and its own text obeys the same number and wording checks as the writer's."""
from fsa.findings.review import markdown_block, parse, review_text_problems

F = {"fiscal_year": 2025, "title": "t", "trigger": "x > 1", "question": "q", "facts": [{"name": "a", "value": 70019.87}]}
GOOD = '{"verdict": "supported", "problems": [], "overlooked_explanations": ["FX balances revalued after devaluation"], "context_for_analyst": "ok"}'


def test_well_formed_reply_parses_and_bad_verdict_does_not():
    assert parse(GOOD)["verdict"] == "supported"
    assert parse(GOOD.replace("supported", "great")) is None
    assert parse("no json") is None


def test_reviewer_text_cannot_introduce_new_numbers_or_accusations():
    d = parse(GOOD)
    assert review_text_problems(F, d) == []
    d["problems"] = ["The draft implies a 43% drop", "looks like manipulation"]
    assert len(review_text_problems(F, d)) == 2


def test_markdown_block_is_empty_without_review_and_hides_failed_notes():
    assert markdown_block({}) == []
    f = {"review": {"verdict": "needs_changes", "problems": ["p"], "overlooked_explanations": [], "context_for_analyst": "", "reviewed_by": ["claude-opus-5-5"], "text_checks": "failed: x"}}
    assert "not shown" in "\n".join(markdown_block(f)) and "Problem" not in "\n".join(markdown_block(f))
