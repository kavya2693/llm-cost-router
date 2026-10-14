"""Inline the exported routing log (and the repo's results.json) into the two slides that
draw them: compositions/s04-routes.html (GRID block) and compositions/value.html (SHEET
block). Renders cannot fetch files, so the data is written into the HTML between markers.

Usage: python3 tools/inject_sheet.py assets/routing_log.json <repo>/outputs/results.json compositions \
         --rows ext-023,cla-021,sum-026,rea-040,rea-021,cla-035
"""

import argparse
import json
import re
from pathlib import Path

TASK_LABEL = {
    "extraction": "order email",
    "classification": "ticket",
    "summarization": "shift report",
    "reasoning": "arithmetic",
}
SHORT_MODEL = {"llama3.2": "llama3.2", "qwen2.5-coder:3b": "qwen 3B", "qwen2.5-coder:7b": "qwen 7B"}
ARMS = [
    ("all-small qwen2.5-coder:3b", "all qwen 3B"),
    ("all-small llama3.2", "all llama3.2"),
    ("all-large qwen2.5-coder:7b", "all 7B"),
    ("router, structural check only", "router, checks only"),
    ("router, tuned on dev", "router, tuned"),
    ("oracle (perfect verifier)", "perfect verifier"),
]


def check_text(r: dict) -> str:
    if not r["check_passed"]:
        return "✗ " + r["check_reason"]
    if r["threshold"] > 0:
        conf = f"{r['confidence']:.5f}".lstrip("0")
        if r["confidence"] >= r["threshold"]:
            return f"✓ conf {conf}"
        return f"✗ conf {conf} < τ"
    return {
        "extraction": "✓ grounded",
        "classification": "✓ one label",
        "summarization": "✓ ≤ 40 words",
        "reasoning": "✓ ANSWER line",
    }[r["task"]]


def answer_text(r: dict) -> str:
    out = r["output"].strip()
    if r["task"] == "extraction":
        o = json.loads(out[out.find("{") : out.rfind("}") + 1])
        return f"{o['order_id']} · {o['quantity']} units"
    if r["task"] == "classification":
        return out.split()[0]
    if r["task"] == "reasoning":
        return "ANSWER " + re.findall(r"ANSWER:\s*([\d,]+)", out, re.I)[-1]
    # summaries: the order count the summary reports (the verifier checks it appears in the report)
    return re.findall(r"\d[\d,]*", out)[0] + " missed cut-off"


def inject(path: Path, name: str, data: dict) -> None:
    html = path.read_text()
    block = (
        f"/* {name}:BEGIN */ const {name} = "
        + json.dumps(data, separators=(",", ":"))
        + f"; /* {name}:END */"
    )
    html, n = re.subn(rf"/\* {name}:BEGIN \*/.*?/\* {name}:END \*/", lambda _: block, html, flags=re.S)
    assert n == 1, f"{name} marker not found in {path}"
    path.write_text(html)


ap = argparse.ArgumentParser()
ap.add_argument("log")
ap.add_argument("results")
ap.add_argument("compositions")
ap.add_argument("--rows", required=True, help="case ids shown as ledger rows, in order")
a = ap.parse_args()

log = json.loads(Path(a.log).read_text())
res = json.loads(Path(a.results).read_text())["splits"]["test"]
by_id = {r["id"]: r for r in log["rows"]}
comp = Path(a.compositions)

grid = {
    "tasks": list(TASK_LABEL),
    "cells": [[r["task"], int(r["escalated"]), int(r["correct"])] for r in log["rows"]],
    "kept": log["answered_small"],
    "escalated": log["escalated"],
    "correct": log["correct"],
    "cases": log["cases"],
    "cost_per_1k": log["cost_per_1k_usd"],
    "arms": [
        {
            "name": label,
            "completion": round(100 * res[k]["completion"], 1),
            "cost": round(res[k]["cost_per_1k_usd"], 4),
        }
        for k, label in ARMS
    ],
}
inject(comp / "s04-routes.html", "GRID", grid)

rows = []
for cid in a.rows.split(","):
    r = by_id[cid]
    rows.append(
        {
            "id": cid,
            "task": r["task"],
            "kind": TASK_LABEL[r["task"]],
            "first": SHORT_MODEL[r["first_model"]],
            "check": check_text(r),
            "by": "7B" if r["escalated"] else "3B",
            "answer": answer_text(r),
            "cost_1k": f"${1000 * r['cost_usd']:.3f}",
        }
    )
large = res["all-large qwen2.5-coder:7b"]["cost_per_1k_usd"]
sheet = {
    "rows": rows,
    "cases": log["cases"],
    "kept": log["answered_small"],
    "escalated": log["escalated"],
    "cost_per_1k": log["cost_per_1k_usd"],
    "large_cost_per_1k": round(large, 4),
}
inject(comp / "value.html", "SHEET", sheet)
print("injected", len(grid["cells"]), "grid cells,", len(rows), "ledger rows")
