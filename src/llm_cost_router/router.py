"""The routing policy: try the cheap model, keep its answer if the verifier is satisfied,
otherwise pay for the large one.

The same code serves live traffic (a caller that hits Ollama) and the evaluation (a
caller that replays the cache), so the numbers in the README come from the code path a
deployment would run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from llm_cost_router.client import Caller, Response
from llm_cost_router.config import LARGE_MODEL, MODELS
from llm_cost_router.verifiers import Verdict, verify


@dataclass(frozen=True)
class TaskPolicy:
    # None sends the task straight to the large model.
    first: str | None
    threshold: float = 0.0


@dataclass
class Routed:
    case_id: str
    output: str
    final_model: str
    calls: list[Response] = field(default_factory=list)
    verdict: Verdict | None = None

    @property
    def escalated(self) -> bool:
        return len(self.calls) > 1

    @property
    def cost_usd(self) -> float:
        return sum(MODELS[c.model].cost(c.prompt_tokens, c.completion_tokens) for c in self.calls)

    @property
    def latency_s(self) -> float:
        # Escalation is sequential: the user waits for the cheap attempt and then the large one.
        return sum(c.latency_s for c in self.calls)


class Router:
    def __init__(self, policies: dict[str, TaskPolicy], call: Caller, large: str = LARGE_MODEL) -> None:
        self.policies = policies
        self.call = call
        self.large = large

    def route(self, case: dict[str, Any]) -> Routed:
        policy = self.policies.get(case["task"], TaskPolicy(first=None))
        calls: list[Response] = []
        verdict = None
        if policy.first is not None:
            first = self.call(policy.first, case)
            calls.append(first)
            verdict = verify(case, first)
            if verdict.passed and verdict.confidence >= policy.threshold:
                return Routed(case["id"], first.text, first.model, calls, verdict)
        final = self.call(self.large, case)
        calls.append(final)
        return Routed(case["id"], final.text, final.model, calls, verdict)
