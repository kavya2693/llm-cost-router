"""`llm-cost-router collect` fills the response cache; `evaluate` replays it."""

from __future__ import annotations

import argparse
import sys

from llm_cost_router.client import ResponseCache, collect
from llm_cost_router.config import CACHE_DIR, LARGE_MODEL, OUTPUT_DIR, SMALL_MODELS, SUITE_PATH, TRAFFIC_PATH
from llm_cost_router.data import load_suite, load_traffic
from llm_cost_router.evaluate import evaluate, write_outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="llm-cost-router")
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="call every model on every case through Ollama")
    c.add_argument("--models", nargs="*", default=[*SMALL_MODELS, LARGE_MODEL])
    c.add_argument("--limit", type=int, default=None, help="first N cases only, for a smoke run")
    sub.add_parser("evaluate", help="replay all arms from the cache and write outputs/")
    args = parser.parse_args(argv)

    suite = load_suite(SUITE_PATH)
    cache = ResponseCache(CACHE_DIR)
    if args.cmd == "collect":
        cases = suite[: args.limit] if args.limit else suite
        made = collect(cases, args.models, cache, log=lambda m: print(m, flush=True))
        print(f"made {made} new calls; cache in {CACHE_DIR}")
        return 0

    traffic = load_traffic(TRAFFIC_PATH) if TRAFFIC_PATH.exists() else None
    try:
        results = evaluate(suite, cache.caller(), traffic)
    except KeyError as e:
        print(e, file=sys.stderr)
        return 1
    write_outputs(results, OUTPUT_DIR)
    print((OUTPUT_DIR / "results.md").read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
