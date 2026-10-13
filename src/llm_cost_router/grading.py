"""Score a model's raw output against the answer the generator wrote into each case.

This is the ground-truth side. The router never sees it; it only decides whether a
request was completed after routing has finished.
"""

from __future__ import annotations

import json
import re
from typing import Any

SUMMARY_WORD_LIMIT = 40
EXTRACTION_FIELDS = ("order_id", "customer", "sku", "quantity", "delivery_city", "delivery_date")
NULL_STRINGS = {"", "null", "none", "n/a", "na", "unknown", "tbc", "not provided", "not specified"}

_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def parse_json_object(text: str) -> dict[str, Any] | None:
    """The first JSON object in the text, tolerating code fences and chatter around it."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def normalise_null(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in NULL_STRINGS:
        return None
    return value


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        digits = value.replace(",", "").strip()
        if re.fullmatch(r"-?\d+", digits):
            return int(digits)
    return None


def _same_text(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return " ".join(str(a).split()).lower() == " ".join(str(b).split()).lower()


def grade_extraction(output: str, answer: dict[str, Any]) -> bool:
    obj = parse_json_object(output)
    if obj is None:
        return False
    for field in EXTRACTION_FIELDS:
        got = normalise_null(obj.get(field))
        want = answer[field]
        if field == "quantity":
            if _as_int(got) != want:
                return False
        elif not _same_text(got, want):
            return False
    return True


def parse_label(output: str, labels: tuple[str, ...]) -> str | None:
    """A label only if the output names exactly one of them."""
    cleaned = output.strip().lower().replace("-", "_").replace(" ", "_").strip(".\"'`*:_ \n")
    if cleaned in labels:
        return cleaned
    found = [lab for lab in labels if re.search(rf"\b{lab}\b", output.lower().replace(" ", "_"))]
    return found[0] if len(found) == 1 else None


def grade_classification(output: str, answer: str, labels: tuple[str, ...]) -> bool:
    return parse_label(output, labels) == answer


def numbers_in(text: str) -> list[int]:
    out = []
    for raw in _NUMBER.findall(text):
        cleaned = raw.replace(",", "")
        if "." in cleaned:
            continue
        out.append(int(cleaned))
    return out


def grade_summary(output: str, answer: dict[str, Any]) -> bool:
    text = output.strip()
    if not text or len(text.split()) > SUMMARY_WORD_LIMIT:
        return False
    low = text.lower()
    if answer["site"].lower() not in low:
        return False
    if answer["affected_orders"] not in numbers_in(text):
        return False
    if not any(k in low for k in answer["cause_keywords"]):
        return False
    return any(k in low for k in answer["action_keywords"])


def parse_final_number(output: str) -> int | None:
    """The number after the last `ANSWER:`, or failing that the last integer in the text."""
    marks = list(re.finditer(r"answer\s*[:=]\s*\**\s*(?:aed\s*)?(-?[\d,]+)", output, re.IGNORECASE))
    if marks:
        return int(marks[-1].group(1).replace(",", ""))
    nums = numbers_in(output)
    return nums[-1] if nums else None


def grade_reasoning(output: str, answer: int) -> bool:
    return parse_final_number(output) == answer


def grade(task: str, output: str, answer: Any) -> bool:
    from llm_cost_router.data import LABELS

    if task == "extraction":
        return grade_extraction(output, answer)
    if task == "classification":
        return grade_classification(output, answer, LABELS)
    if task == "summarization":
        return grade_summary(output, answer)
    if task == "reasoning":
        return grade_reasoning(output, answer)
    raise ValueError(f"unknown task {task}")
