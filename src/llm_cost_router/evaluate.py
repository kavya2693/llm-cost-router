"""Replay every arm over the cached responses, tune the router on the dev split, and
report completion, cost and latency on the held-out split."""

from __future__ import annotations

import csv
import datetime as dt
import json
import random
import statistics
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from llm_cost_router.client import Caller
from llm_cost_router.config import LARGE_MODEL, MODELS, SMALL_MODELS, TASK_TYPES
from llm_cost_router.grading import grade
from llm_cost_router.router import Routed, Router, TaskPolicy
from llm_cost_router.verifiers import verify


@dataclass
class Outcome:
    case: dict[str, Any]
    routed: Routed
    correct: bool


def run_arm(router: Router, cases: Iterable[dict[str, Any]]) -> list[Outcome]:
    out = []
    for case in cases:
        routed = router.route(case)
        out.append(Outcome(case, routed, grade(case["task"], routed.output, case["answer"])))
    return out


def single_model_router(model: str, call: Caller) -> Router:
    return Router({}, call, large=model)


def oracle(cases: Iterable[dict[str, Any]], call: Caller, models: Iterable[str]) -> list[Outcome]:
    """The cheapest single call that was right, as if the verifier were perfect."""
    out = []
    order = sorted(models, key=lambda m: MODELS[m].usd_per_m_output)
    for case in cases:
        chosen = None
        for m in order:
            r = call(m, case)
            if grade(case["task"], r.text, case["answer"]):
                chosen = r
                break
        r = chosen or call(LARGE_MODEL, case)
        routed = Routed(case["id"], r.text, r.model, [r])
        out.append(Outcome(case, routed, chosen is not None))
    return out


def percentile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def summarise(outcomes: list[Outcome]) -> dict[str, Any]:
    n = len(outcomes)
    lat = [o.routed.latency_s for o in outcomes]
    accepted_cheap = [o for o in outcomes if o.routed.calls and o.routed.final_model != LARGE_MODEL]
    tried_cheap = [o for o in outcomes if o.routed.verdict is not None]
    return {
        "n": n,
        "completion": sum(o.correct for o in outcomes) / n if n else 0.0,
        "cost_per_1k_usd": 1000 * sum(o.routed.cost_usd for o in outcomes) / n if n else 0.0,
        "tokens_per_request": sum(
            c.prompt_tokens + c.completion_tokens for o in outcomes for c in o.routed.calls
        )
        / max(n, 1),
        "p50_latency_s": percentile(lat, 0.5),
        "p95_latency_s": percentile(lat, 0.95),
        "share_answered_small": len(accepted_cheap) / n if n else 0.0,
        "escalation_rate": (sum(o.routed.escalated for o in tried_cheap) / len(tried_cheap))
        if tried_cheap
        else 0.0,
        # Wrong answers the verifier let through: the cost of trusting the cheap model.
        "wrong_accepted_small": sum(
            1 for o in accepted_cheap if not o.correct and o.routed.verdict is not None
        ),
    }


def breakdown(outcomes: list[Outcome], key: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[Outcome]] = {}
    for o in outcomes:
        groups.setdefault(o.case[key], []).append(o)
    return {k: summarise(v) for k, v in sorted(groups.items())}


def calibrate(
    dev: list[dict[str, Any]], call: Caller, small_models: tuple[str, ...] = SMALL_MODELS
) -> tuple[dict[str, TaskPolicy], dict[str, Any]]:
    """Per task type, the cheapest policy that completes at least as many dev cases as the
    large model alone. Thresholds are read off the dev confidences, never the test split."""
    policies: dict[str, TaskPolicy] = {}
    trace: dict[str, Any] = {}
    for task in TASK_TYPES:
        cases = [c for c in dev if c["task"] == task]
        large = summarise(run_arm(single_model_router(LARGE_MODEL, call), cases))
        options = [TaskPolicy(first=None)]
        for m in small_models:
            confs = sorted({round(verify(c, call(m, c)).confidence, 4) for c in cases})
            options += [TaskPolicy(m, 0.0)] + [TaskPolicy(m, t) for t in confs if t > 0]
        best, best_key, best_stats = options[0], (large["cost_per_1k_usd"], -large["completion"], 0.0), large
        for p in options[1:]:
            s = summarise(run_arm(Router({task: p}, call), cases))
            if s["completion"] + 1e-9 < large["completion"]:
                continue
            key = (s["cost_per_1k_usd"], -s["completion"], p.threshold)
            if key < best_key:
                best, best_key, best_stats = p, key, s
        policies[task] = best
        trace[task] = {
            "first": best.first,
            "threshold": best.threshold,
            "dev_large_completion": large["completion"],
            "dev_completion": best_stats["completion"],
            "dev_cost_per_1k_usd": best_stats["cost_per_1k_usd"],
            "dev_large_cost_per_1k_usd": large["cost_per_1k_usd"],
        }
    return policies, trace


def paired_bootstrap(a: list[Outcome], b: list[Outcome], seed: int = 0, reps: int = 4000) -> list[float]:
    """95% interval for completion(a) - completion(b) over the same cases."""
    rng = random.Random(seed)
    diffs = [int(x.correct) - int(y.correct) for x, y in zip(a, b, strict=True)]
    n = len(diffs)
    stats = sorted(sum(diffs[rng.randrange(n)] for _ in range(n)) / n for _ in range(reps))
    return [stats[int(0.025 * reps)], stats[int(0.975 * reps)]]


def annual_volume(traffic: list[dict[str, Any]]) -> dict[str, float]:
    """Yearly requests per task, filling logging gaps with the same weekday's nearby mean."""
    totals = dict.fromkeys(TASK_TYPES, 0.0)
    dates = [dt.date.fromisoformat(r["date"]) for r in traffic]
    for i, row in enumerate(traffic):
        for t in TASK_TYPES:
            if row[t] != "":
                totals[t] += float(row[t])
                continue
            near = [
                float(traffic[j][t])
                for j in range(max(0, i - 28), min(len(traffic), i + 29))
                if traffic[j][t] != "" and dates[j].weekday() == dates[i].weekday()
            ]
            totals[t] += statistics.mean(near) if near else 0.0
    return totals


def projected_annual_cost(outcomes: list[Outcome], volume: dict[str, float]) -> float:
    by_task = breakdown(outcomes, "task")
    return float(sum(by_task[t]["cost_per_1k_usd"] / 1000 * volume[t] for t in TASK_TYPES if t in by_task))


def evaluate(
    suite: list[dict[str, Any]],
    call: Caller,
    traffic: list[dict[str, Any]] | None = None,
    small_models: tuple[str, ...] = SMALL_MODELS,
) -> dict[str, Any]:
    dev = [c for c in suite if c["split"] == "dev"]
    test = [c for c in suite if c["split"] == "test"]
    tuned, trace = calibrate(dev, call, small_models)
    check_only = {t: TaskPolicy(small_models[0], 0.0) for t in TASK_TYPES}

    arms: dict[str, Router | None] = {f"all-small {m}": single_model_router(m, call) for m in small_models}
    arms |= {
        "all-large qwen2.5-coder:7b": single_model_router(LARGE_MODEL, call),
        "router, structural check only": Router(check_only, call),
        "router, tuned on dev": Router(tuned, call),
        "oracle (perfect verifier)": None,
    }
    volume = annual_volume(traffic) if traffic else None
    results: dict[str, Any] = {"policy": trace, "splits": {}}
    per_case: list[dict[str, Any]] = []
    for split_name, cases in (("test", test), ("all", suite)):
        split: dict[str, Any] = {}
        outcomes_by_arm: dict[str, list[Outcome]] = {}
        for name, router in arms.items():
            outcomes = (
                oracle(cases, call, [*small_models, LARGE_MODEL])
                if router is None
                else run_arm(router, cases)
            )
            outcomes_by_arm[name] = outcomes
            entry = summarise(outcomes)
            entry["by_task"] = breakdown(outcomes, "task")
            entry["by_difficulty"] = breakdown(outcomes, "difficulty")
            if volume:
                entry["projected_annual_cost_usd"] = projected_annual_cost(outcomes, volume)
            split[name] = entry
            if split_name == "test":
                for o in outcomes:
                    per_case.append(
                        {
                            "arm": name,
                            "id": o.case["id"],
                            "task": o.case["task"],
                            "difficulty": o.case["difficulty"],
                            "final_model": o.routed.final_model,
                            "escalated": o.routed.escalated,
                            "correct": o.correct,
                            "cost_usd": round(o.routed.cost_usd, 8),
                            "latency_s": round(o.routed.latency_s, 3),
                        }
                    )
        large = outcomes_by_arm["all-large qwen2.5-coder:7b"]
        split["router_minus_large_ci95"] = paired_bootstrap(outcomes_by_arm["router, tuned on dev"], large)
        results["splits"][split_name] = split
    results["per_case"] = per_case
    # Total inference time per model over the whole suite: what a fresh `make models` costs.
    results["model_seconds"] = {
        m: round(sum(call(m, c).latency_s for c in suite), 1) for m in [*small_models, LARGE_MODEL]
    }
    if volume:
        results["annual_requests"] = volume
    return results


def _fmt_row(name: str, s: dict[str, Any], large: dict[str, Any]) -> str:
    saving = 1 - s["cost_per_1k_usd"] / large["cost_per_1k_usd"] if large["cost_per_1k_usd"] else 0.0
    return (
        f"| {name} | {s['completion']:.1%} | {s['by_difficulty'].get('hard', {}).get('completion', 0):.1%} "
        f"| ${s['cost_per_1k_usd']:.4f} | {saving:+.0%} "
        f"| {s['p50_latency_s']:.1f} | {s['p95_latency_s']:.1f} "
        f"| {s['share_answered_small']:.0%} | {s['wrong_accepted_small']} |"
    )


def render_markdown(results: dict[str, Any]) -> str:
    lines = ["# Results", ""]
    for split_name, title in (("test", "Held-out split (120 cases)"), ("all", "All 200 cases")):
        split = results["splits"][split_name]
        large = split["all-large qwen2.5-coder:7b"]
        lines += [
            f"## {title}",
            "",
            "| Arm | Completion | Hard cases | Cost / 1k req | vs all-large | p50 s | p95 s "
            "| Answered by small | Wrong answers let through |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for name, s in split.items():
            if isinstance(s, dict) and "completion" in s:
                lines.append(_fmt_row(name, s, large))
        lo, hi = split["router_minus_large_ci95"]
        lines += [
            "",
            f"Router minus all-large completion, paired bootstrap 95% CI: [{lo:+.1%}, {hi:+.1%}]",
            "",
        ]
        lines += ["| Arm | " + " | ".join(TASK_TYPES) + " |", "|---|" + "---|" * len(TASK_TYPES)]
        for name, s in split.items():
            if isinstance(s, dict) and "by_task" in s:
                cells = [
                    f"{s['by_task'][t]['completion']:.0%} (${s['by_task'][t]['cost_per_1k_usd']:.3f})"
                    for t in TASK_TYPES
                ]
                lines.append(f"| {name} | " + " | ".join(cells) + " |")
        lines.append("")
        if "projected_annual_cost_usd" in large:
            lines += ["| Arm | Projected annual cost at logged traffic |", "|---|---|"]
            for name, s in split.items():
                if isinstance(s, dict) and "projected_annual_cost_usd" in s:
                    lines.append(f"| {name} | ${s['projected_annual_cost_usd']:,.0f} |")
            lines.append("")
    lines += [
        "## Tuned policy (chosen on the dev split)",
        "",
        "| Task | First model | Threshold |",
        "|---|---|---|",
    ]
    for t, p in results["policy"].items():
        lines.append(f"| {t} | {p['first'] or 'straight to large'} | {p['threshold']:.3f} |")
    if "model_seconds" in results:
        lines += ["", "| Model | Inference time for all 200 cases on this machine |", "|---|---|"]
        for m, secs in results["model_seconds"].items():
            lines.append(f"| {m} | {secs / 60:.1f} min |")
    if "annual_requests" in results:
        total = sum(results["annual_requests"].values())
        lines += ["", f"Annual request volume from the traffic log: {total:,.0f}"]
    return "\n".join(lines) + "\n"


def write_outputs(results: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    per_case = results.pop("per_case")
    with (out_dir / "per_case.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(per_case[0]))
        writer.writeheader()
        writer.writerows(per_case)
    (out_dir / "results.json").write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    (out_dir / "results.md").write_text(render_markdown(results))
    results["per_case"] = per_case
