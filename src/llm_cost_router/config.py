"""Models, prices and paths shared by the collector, the router and the evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "outputs"
SUITE_PATH = DATA_DIR / "eval_suite.jsonl"
TRAFFIC_PATH = DATA_DIR / "traffic_daily.csv"
CACHE_DIR = OUTPUT_DIR / "responses"

SEED = 20261013
TASK_TYPES = ("extraction", "classification", "summarization", "reasoning")


@dataclass(frozen=True)
class Model:
    name: str
    tier: str
    params_b: float
    # USD per million tokens. Self-hosted models have no list price, so these follow what
    # hosted inference providers typically charge for open models of the same size class.
    # The ratio between tiers matters more than the absolute level.
    usd_per_m_input: float
    usd_per_m_output: float

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (prompt_tokens * self.usd_per_m_input + completion_tokens * self.usd_per_m_output) / 1e6


MODELS: dict[str, Model] = {
    "llama3.2": Model("llama3.2", "small", 3.2, 0.06, 0.06),
    "qwen2.5-coder:3b": Model("qwen2.5-coder:3b", "small", 3.1, 0.06, 0.06),
    "qwen2.5-coder:7b": Model("qwen2.5-coder:7b", "large", 7.6, 0.20, 0.20),
}
SMALL_MODELS = ("qwen2.5-coder:3b", "llama3.2")
LARGE_MODEL = "qwen2.5-coder:7b"

OLLAMA_URL = "http://localhost:11434"
# Output caps keep a full collection run under an hour on a CPU-only laptop.
MAX_TOKENS = {"extraction": 160, "classification": 8, "summarization": 90, "reasoning": 220}
