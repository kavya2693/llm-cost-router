"""Router, verifiers and evaluation, with every model call stubbed."""

import json
import math

from llm_cost_router.client import Response, ResponseCache, collect
from llm_cost_router.config import LARGE_MODEL, MODELS, TASK_TYPES
from llm_cost_router.data import generate_suite
from llm_cost_router.evaluate import calibrate, evaluate, render_markdown
from llm_cost_router.prompts import build_prompt, prompt_hash
from llm_cost_router.router import Router, TaskPolicy
from llm_cost_router.verifiers import verify

SUITE = generate_suite()
SMALL = "qwen2.5-coder:3b"


def perfect_text(case: dict) -> str:
    a = case["answer"]
    if case["task"] == "extraction":
        return json.dumps(a)
    if case["task"] == "classification":
        return a
    if case["task"] == "summarization":
        return (
            f"At {a['site']}, {a['affected_orders']} orders missed cut-off ({a['cause_keywords'][0]}); "
            f"the manager {a['action_keywords'][0]}."
        )
    return f"Steps done.\nANSWER: {a}"


def wrong_text(case: dict) -> str:
    a = case["answer"]
    if case["task"] == "extraction":
        return json.dumps({**a, "quantity": a["quantity"] + 1})
    if case["task"] == "classification":
        return "billing" if a != "billing" else "shipping"
    if case["task"] == "summarization":
        return f"At {a['site']}, some orders were late."
    return f"ANSWER: {a + 1}"


def make_response(model: str, case: dict, text: str, logprob: float = -0.01) -> Response:
    tokens = [(text[i : i + 4], logprob) for i in range(0, len(text), 4)] or [("", logprob)]
    return Response(
        case_id=case["id"],
        model=model,
        prompt_hash=prompt_hash(build_prompt(case)),
        text=text,
        prompt_tokens=100,
        completion_tokens=len(tokens),
        latency_s=1.0 if MODELS[model].tier == "small" else 3.0,
        token_logprobs=tokens,
        first_top=[[(text[:4], logprob)]],
    )


class StubModels:
    """Large is always right; small is right on easy cases, wrong (but confident) on hard ones."""

    def __init__(self, small_confident_when_wrong: bool = True) -> None:
        self.calls: list[tuple[str, str]] = []
        self.small_confident_when_wrong = small_confident_when_wrong

    def __call__(self, model: str, case: dict) -> Response:
        self.calls.append((model, case["id"]))
        if model == LARGE_MODEL or case["difficulty"] == "easy":
            return make_response(model, case, perfect_text(case), -0.01)
        lp = -0.01 if self.small_confident_when_wrong else -3.0
        return make_response(model, case, wrong_text(case), lp)


def test_router_keeps_a_verified_cheap_answer_and_skips_the_large_model():
    stub = StubModels()
    router = Router({t: TaskPolicy(SMALL, 0.0) for t in TASK_TYPES}, stub)
    case = next(c for c in SUITE if c["task"] == "extraction" and c["difficulty"] == "easy")
    routed = router.route(case)
    assert routed.final_model == SMALL
    assert stub.calls == [(SMALL, case["id"])]
    assert math.isclose(routed.cost_usd, MODELS[SMALL].cost(100, routed.calls[0].completion_tokens))


def test_router_escalates_when_the_structural_check_fails():
    stub = StubModels()
    router = Router({"summarization": TaskPolicy(SMALL, 0.0)}, stub)
    case = next(c for c in SUITE if c["task"] == "summarization" and c["difficulty"] == "hard")
    routed = router.route(case)
    # the wrong summary carries no order count, so the verifier sends it up
    assert routed.final_model == LARGE_MODEL and routed.escalated
    assert routed.latency_s == 4.0


def test_confidence_threshold_catches_what_the_schema_check_cannot():
    stub = StubModels(small_confident_when_wrong=False)
    case = next(c for c in SUITE if c["task"] == "reasoning" and c["difficulty"] == "hard")
    loose = Router({"reasoning": TaskPolicy(SMALL, 0.0)}, stub).route(case)
    strict = Router({"reasoning": TaskPolicy(SMALL, 0.5)}, stub).route(case)
    assert loose.final_model == SMALL  # a well-formed wrong answer passes the format check
    assert strict.final_model == LARGE_MODEL


def test_extraction_verifier_rejects_values_not_in_the_email():
    case = next(c for c in SUITE if c["task"] == "extraction" and c["difficulty"] == "hard")
    good = make_response(SMALL, case, json.dumps(case["answer"]))
    invented = make_response(SMALL, case, json.dumps({**case["answer"], "sku": "XXX-0000"}))
    assert verify(case, good).passed
    assert not verify(case, invented).passed


def test_calibration_never_trades_completion_for_cost_on_dev():
    stub = StubModels(small_confident_when_wrong=False)
    dev = [c for c in SUITE if c["split"] == "dev"]
    policies, trace = calibrate(dev, stub)
    for t in TASK_TYPES:
        assert trace[t]["dev_completion"] >= trace[t]["dev_large_completion"]
        assert trace[t]["dev_cost_per_1k_usd"] <= trace[t]["dev_large_cost_per_1k_usd"]
    # with confidence that separates right from wrong, every task can start small
    assert all(p.first is not None for p in policies.values())


def test_evaluate_end_to_end_on_stubs():
    stub = StubModels(small_confident_when_wrong=False)
    results = evaluate(SUITE, stub)
    test = results["splits"]["test"]
    router, large = test["router, tuned on dev"], test["all-large qwen2.5-coder:7b"]
    assert router["completion"] == large["completion"] == 1.0
    assert router["cost_per_1k_usd"] < large["cost_per_1k_usd"]
    assert test["all-small qwen2.5-coder:3b"]["completion"] == 0.5
    assert "| router, tuned on dev |" in render_markdown(results)


def test_cache_round_trip_and_resume(tmp_path):
    stub = StubModels()
    cases = SUITE[:3]
    cache = ResponseCache(tmp_path)
    assert collect(cases, [SMALL], cache, call=stub, log=lambda _: None) == 3
    reloaded = ResponseCache(tmp_path)
    assert collect(cases, [SMALL], reloaded, call=stub, log=lambda _: None) == 0
    assert reloaded.caller()(SMALL, cases[0]).text == perfect_text(cases[0])
