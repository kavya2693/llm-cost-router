"""Model calls: a thin Ollama client and a response cache that replays them.

Every call made by the collector is appended to outputs/responses/<model>.jsonl. The
evaluation only ever reads that cache, which is why its numbers reproduce exactly and
why CI needs no model server.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from llm_cost_router.config import MAX_TOKENS, OLLAMA_URL
from llm_cost_router.prompts import JSON_MODE_TASKS, build_prompt, prompt_hash


@dataclass
class Response:
    case_id: str
    model: str
    prompt_hash: str
    text: str
    prompt_tokens: int
    completion_tokens: int
    latency_s: float
    # Per output token: (token, logprob). The first few tokens also keep their alternatives,
    # which is what the classification confidence reads.
    token_logprobs: list[tuple[str, float]] = field(default_factory=list)
    first_top: list[list[tuple[str, float]]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Response:
        d = dict(d)
        d["token_logprobs"] = [tuple(x) for x in d.get("token_logprobs", [])]
        d["first_top"] = [[tuple(x) for x in pos] for pos in d.get("first_top", [])]
        return cls(**d)


Caller = Callable[[str, dict[str, Any]], Response]


def ollama_call(model: str, case: dict[str, Any], url: str | None = None, timeout: float = 600) -> Response:
    prompt = build_prompt(case)
    options: dict[str, Any] = {"temperature": 0, "seed": 7, "num_predict": MAX_TOKENS[case["task"]]}
    # macOS 13 crashes on the Metal path for these models; OLLAMA_NUM_GPU=0 forces CPU.
    if os.environ.get("OLLAMA_NUM_GPU"):
        options["num_gpu"] = int(os.environ["OLLAMA_NUM_GPU"])
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "logprobs": True,
        "top_logprobs": 5,
        "keep_alive": "30m",
        "options": options,
    }
    if case["task"] in JSON_MODE_TASKS:
        body["format"] = "json"
    req = urllib.request.Request(
        (url or os.environ.get("OLLAMA_URL", OLLAMA_URL)) + "/api/chat",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    wall = time.perf_counter() - started
    lps = d.get("logprobs") or []
    # Model load time is a one-off per model, not a per-request cost.
    latency = (d.get("total_duration", 0) - d.get("load_duration", 0)) / 1e9 or wall
    return Response(
        case_id=case["id"],
        model=model,
        prompt_hash=prompt_hash(prompt),
        text=d["message"]["content"],
        prompt_tokens=int(d.get("prompt_eval_count", 0)),
        completion_tokens=int(d.get("eval_count", 0)),
        latency_s=round(latency, 4),
        token_logprobs=[(x["token"], round(x["logprob"], 5)) for x in lps],
        first_top=[
            [(t["token"], round(t["logprob"], 5)) for t in x.get("top_logprobs", [])] for x in lps[:3]
        ],
    )


class ResponseCache:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._store: dict[tuple[str, str], Response] = {}
        if directory.exists():
            for path in sorted(directory.glob("*.jsonl")):
                with path.open() as f:
                    for line in f:
                        if line.strip():
                            r = Response.from_dict(json.loads(line))
                            self._store[(r.model, r.case_id)] = r

    def _path(self, model: str) -> Path:
        return self.directory / (model.replace(":", "_").replace("/", "_") + ".jsonl")

    def get(self, model: str, case: dict[str, Any]) -> Response | None:
        r = self._store.get((model, case["id"]))
        # A changed prompt invalidates the cached answer.
        if r is None or r.prompt_hash != prompt_hash(build_prompt(case)):
            return None
        return r

    def put(self, r: Response) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        self._store[(r.model, r.case_id)] = r
        with self._path(r.model).open("a") as f:
            f.write(json.dumps(asdict(r)) + "\n")

    def caller(self) -> Caller:
        def call(model: str, case: dict[str, Any]) -> Response:
            r = self.get(model, case)
            if r is None:
                raise KeyError(f"no cached response for {model} on {case['id']}; run `make models`")
            return r

        return call


def collect(
    cases: list[dict[str, Any]],
    models: list[str],
    cache: ResponseCache,
    call: Caller = ollama_call,
    log: Callable[[str], None] = print,
) -> int:
    """Fill the cache, one model at a time so each model loads once. Safe to resume."""
    made = 0
    for model in models:
        # Round-robin over task types so a partial run still covers every task.
        todo = sorted((c for c in cases if cache.get(model, c) is None), key=lambda c: c["id"][-3:])
        log(f"{model}: {len(todo)} of {len(cases)} cases to run")
        for i, case in enumerate(todo, 1):
            cache.put(call(model, case))
            made += 1
            if i % 25 == 0:
                log(f"  {model}: {i}/{len(todo)}")
    return made
