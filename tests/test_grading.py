"""The scorer is checked against deliberately broken outputs before any model is called."""

import json

import pytest

from llm_cost_router.config import SUITE_PATH
from llm_cost_router.data import LABELS, generate_suite
from llm_cost_router.grading import grade, parse_final_number, parse_label

SUITE = generate_suite()


def _first(task: str, difficulty: str = "hard") -> dict:
    return next(c for c in SUITE if c["task"] == task and c["difficulty"] == difficulty)


def test_suite_shape_and_determinism():
    assert len(SUITE) == 200
    assert generate_suite() == SUITE
    counts = {t: sum(c["task"] == t for c in SUITE) for t in ("extraction", "classification")}
    assert counts == {"extraction": 50, "classification": 50}
    assert sum(c["split"] == "test" for c in SUITE) == 120


def test_committed_suite_matches_generator():
    if not SUITE_PATH.exists():
        pytest.skip("run `make data` first")
    with SUITE_PATH.open() as f:
        assert [json.loads(line) for line in f] == SUITE


def test_perfect_outputs_score_complete():
    for case in SUITE:
        task, ans = case["task"], case["answer"]
        if task == "extraction":
            out = "```json\n" + json.dumps(ans) + "\n```"
        elif task == "classification":
            out = ans
        elif task == "summarization":
            out = (
                f"{ans['site']}: {ans['affected_orders']:,} orders missed cut-off, "
                f"{ans['cause_keywords'][0]}; manager {ans['action_keywords'][0]}."
            )
        else:
            out = f"step one... ANSWER: {ans:,}"
        assert grade(task, out, ans), case["id"]


def test_extraction_rejects_the_distractor_quantity_and_wrong_null():
    case = _first("extraction")
    ans = case["answer"]
    assert not grade("extraction", json.dumps({**ans, "quantity": ans["quantity"] + 1}), ans)
    assert not grade("extraction", json.dumps({**ans, "order_id": "PO-00000"}), ans)
    missing_date = {**ans, "delivery_date": None}
    if ans["delivery_date"] is not None:
        assert not grade("extraction", json.dumps(missing_date), ans)
    assert not grade("extraction", "not json at all", ans)
    no_date = next(c for c in SUITE if c["task"] == "extraction" and c["answer"]["delivery_date"] is None)
    assert grade("extraction", json.dumps({**no_date["answer"], "delivery_date": "N/A"}), no_date["answer"])


def test_classification_needs_exactly_one_label():
    assert parse_label("Shipping.", LABELS) == "shipping"
    assert parse_label("account access", LABELS) == "account_access"
    assert parse_label("billing or returns", LABELS) is None
    assert parse_label("refund", LABELS) is None


def test_summary_rejects_wrong_count_and_overlong_text():
    case = _first("summarization")
    ans = case["answer"]
    cause, action = ans["cause_keywords"][0], ans["action_keywords"][0]
    good = f"{ans['site']} had {ans['affected_orders']} late orders ({cause}); {action}."
    assert grade("summarization", good, ans)
    assert not grade("summarization", good.replace(str(ans["affected_orders"]), "12"), ans)
    assert not grade("summarization", good + " filler" * 40, ans)
    assert not grade("summarization", good.replace(ans["site"], "the site"), ans)


def test_reasoning_reads_the_last_answer_line():
    assert parse_final_number("ANSWER: 12\nwait, recheck. ANSWER: 1,450") == 1450
    assert parse_final_number("So the total is 3492 AED.") == 3492
    assert parse_final_number("Answer: **AED 88**") == 88
    assert parse_final_number("so it is \\(\\boxed{2875}\\).") == 2875
    assert parse_final_number("no idea") is None
