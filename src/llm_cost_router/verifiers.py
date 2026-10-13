"""Cheap checks the router can run on an answer without knowing the right answer.

Each verifier returns whether the output is well formed and grounded in the request,
plus a confidence read from the model's own token probabilities. The router accepts an
answer only if the structural check passes and the confidence clears the threshold
tuned for that task type.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass
from typing import Any

from llm_cost_router.client import Response
from llm_cost_router.data import LABELS, MONTHS
from llm_cost_router.grading import (
    EXTRACTION_FIELDS,
    SUMMARY_WORD_LIMIT,
    normalise_null,
    numbers_in,
    parse_json_object,
    parse_label,
)


@dataclass(frozen=True)
class Verdict:
    passed: bool
    confidence: float
    reason: str = ""


def mean_logprob_confidence(r: Response) -> float:
    """Geometric-mean token probability over the whole answer."""
    if not r.token_logprobs:
        return 0.0
    return math.exp(sum(lp for _, lp in r.token_logprobs) / len(r.token_logprobs))


def _mentioned(value: str, text: str) -> bool:
    return " ".join(value.split()).lower() in " ".join(text.split()).lower()


def _date_grounded(iso: str, text: str) -> bool:
    try:
        d = dt.date.fromisoformat(iso)
    except ValueError:
        return False
    if iso in text:
        return True
    month = MONTHS[d.month - 1][:3]
    return bool(re.search(rf"\b{d.day}\b", text)) and month in text and str(d.year) in text


def verify_extraction(case: dict[str, Any], r: Response) -> Verdict:
    obj = parse_json_object(r.text)
    conf = mean_logprob_confidence(r)
    if obj is None:
        return Verdict(False, conf, "not a JSON object")
    if set(obj) != set(EXTRACTION_FIELDS):
        return Verdict(False, conf, "wrong keys")
    text = case["input"]
    for key in ("order_id", "customer", "sku", "delivery_city"):
        val = normalise_null(obj[key])
        if not isinstance(val, str) or not _mentioned(val, text):
            return Verdict(False, conf, f"{key} not found in the email")
    qty = obj["quantity"]
    if not isinstance(qty, int) or isinstance(qty, bool) or qty <= 0 or qty not in numbers_in(text):
        return Verdict(False, conf, "quantity not an integer from the email")
    date = normalise_null(obj["delivery_date"])
    if date is not None and not (isinstance(date, str) and _date_grounded(date, text)):
        return Verdict(False, conf, "delivery date not found in the email")
    return Verdict(True, conf)


def label_confidence(r: Response, label: str) -> float:
    """Probability mass the first token puts on spellings of the chosen label."""
    if not r.first_top:
        return 0.0
    mass = 0.0
    for token, lp in r.first_top[0]:
        t = token.strip().lower().replace(" ", "_")
        if t and label.startswith(t):
            mass += math.exp(lp)
    return min(mass, 1.0)


def verify_classification(case: dict[str, Any], r: Response) -> Verdict:
    label = parse_label(r.text, LABELS)
    if label is None:
        return Verdict(False, 0.0, "not exactly one known label")
    return Verdict(True, label_confidence(r, label))


def verify_summary(case: dict[str, Any], r: Response) -> Verdict:
    text = r.text.strip()
    conf = mean_logprob_confidence(r)
    if not text:
        return Verdict(False, conf, "empty")
    if len(text.split()) > SUMMARY_WORD_LIMIT:
        return Verdict(False, conf, "over the word limit")
    nums = numbers_in(text)
    if not nums:
        return Verdict(False, conf, "no order count")
    source = set(numbers_in(case["input"]))
    if any(n not in source for n in nums):
        return Verdict(False, conf, "number not in the report")
    return Verdict(True, conf)


def answer_confidence(r: Response) -> float:
    """Lowest token probability among the digits after the final ANSWER marker."""
    toks = r.token_logprobs
    joined = "".join(t for t, _ in toks)
    idx = joined.lower().rfind("answer")
    if idx == -1:
        return 0.0
    pos, lows = 0, []
    for t, lp in toks:
        if pos >= idx and any(ch.isdigit() for ch in t):
            lows.append(lp)
        pos += len(t)
    return math.exp(min(lows)) if lows else 0.0


def verify_reasoning(case: dict[str, Any], r: Response) -> Verdict:
    m = list(re.finditer(r"answer\s*[:=]\s*\**\s*(?:aed\s*)?(-?[\d,]+)", r.text, re.IGNORECASE))
    if not m:
        return Verdict(False, 0.0, "no ANSWER line")
    if r.completion_tokens and r.text.rstrip()[-1:] not in "0123456789.*":
        # Truncated or kept talking after the answer: treat as unfinished.
        return Verdict(False, answer_confidence(r), "did not finish on the answer")
    return Verdict(True, answer_confidence(r))


VERIFIERS = {
    "extraction": verify_extraction,
    "classification": verify_classification,
    "summarization": verify_summary,
    "reasoning": verify_reasoning,
}


def verify(case: dict[str, Any], r: Response) -> Verdict:
    return VERIFIERS[case["task"]](case, r)
