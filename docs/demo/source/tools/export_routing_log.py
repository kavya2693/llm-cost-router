"""Re-run the repo's own router over the held-out split and export its routing log.

Same suite, response cache, calibration (dev split) and tuned policy as `make run`
(`llm-cost-router evaluate`). For every held-out case it records what the router did:
which small model went first, the verifier's verdict and confidence, whether the request
was escalated to the 7B model, the final answer, its cost and latency, and whether the
answer key graded it correct. The router never sees the grade; it is added afterwards.

Run:  cd <repo> && .venv/bin/python <this file> <out.json>
"""

import json
import sys
from pathlib import Path

from llm_cost_router.client import ResponseCache
from llm_cost_router.config import CACHE_DIR, LARGE_MODEL, MODELS, SMALL_MODELS, SUITE_PATH
from llm_cost_router.data import load_suite
from llm_cost_router.evaluate import calibrate
from llm_cost_router.grading import grade
from llm_cost_router.router import Router

suite = load_suite(SUITE_PATH)
call = ResponseCache(CACHE_DIR).caller()
dev = [c for c in suite if c["split"] == "dev"]
test = [c for c in suite if c["split"] == "test"]
policies, _trace = calibrate(dev, call, SMALL_MODELS)
router = Router(policies, call)

rows = []
for case in test:
    r = router.route(case)
    first = r.calls[0]
    rows.append(
        {
            "id": case["id"],
            "task": case["task"],
            "difficulty": case["difficulty"],
            "input": case["input"],
            "first_model": policies[case["task"]].first,
            "threshold": policies[case["task"]].threshold,
            "first_output": first.text,
            "check_passed": bool(r.verdict.passed) if r.verdict else None,
            "check_reason": r.verdict.reason if r.verdict else "",
            "confidence": round(r.verdict.confidence, 6) if r.verdict else None,
            "escalated": r.escalated,
            "final_model": r.final_model,
            "output": r.output,
            "answer": case["answer"],
            "correct": grade(case["task"], r.output, case["answer"]),
            "cost_usd": round(r.cost_usd, 8),
            "latency_s": round(r.latency_s, 3),
        }
    )

n = len(rows)
out = {
    "split": "test",
    "cases": n,
    "policy": {t: {"first": p.first, "threshold": p.threshold} for t, p in policies.items()},
    "prices_usd_per_m": {m: MODELS[m].usd_per_m_output for m in MODELS},
    "large_model": LARGE_MODEL,
    "answered_small": sum(not x["escalated"] for x in rows),
    "escalated": sum(x["escalated"] for x in rows),
    "correct": sum(x["correct"] for x in rows),
    "cost_per_1k_usd": round(1000 * sum(x["cost_usd"] for x in rows) / n, 6),
    "rows": rows,
}
Path(sys.argv[1]).write_text(json.dumps(out, indent=1))
print({k: v for k, v in out.items() if k != "rows"})
