"""One prompt per task type. Every model gets the same prompt, so arms differ only by model."""

from __future__ import annotations

import hashlib
from typing import Any

from llm_cost_router.data import LABELS
from llm_cost_router.grading import SUMMARY_WORD_LIMIT

EXTRACTION = """Extract the purchase order from the email below.
Return only a JSON object, no other text, with exactly these keys:
  "order_id"      the order number being placed now (format PO-12345)
  "customer"      the customer company name
  "sku"           the product SKU
  "quantity"      the final quantity requested, as an integer
  "delivery_city" the city of the delivery site
  "delivery_date" the requested delivery date as YYYY-MM-DD, or null if no date is given

Email:
{text}"""

CLASSIFICATION = """Classify the customer support ticket by what the customer is asking us to do.
Labels:
  billing           charges, refunds of money, invoices, fees
  shipping          where an order is, delivery delays, wrong address
  returns           sending an item back or replacing a faulty item
  account_access    logging in, passwords, locked accounts, two-factor codes
  product_question  questions about a product before or after buying
  cancellation      stopping an order or subscription before it ships or renews

Ticket: {text}

Reply with the label only."""

SUMMARIZATION = """Summarise this warehouse shift report in one sentence of at most {limit} words.
The sentence must name the site, the number of orders that missed cut-off, the cause and
the action taken. Ignore routine details.

Report: {text}"""

REASONING = """Solve the problem in plain text. Do not write code and do not use LaTeX.
Show the working in at most four short lines, then finish with a line of the form
ANSWER: <integer>

Problem: {text}"""

TEMPLATES = {
    "extraction": EXTRACTION,
    "classification": CLASSIFICATION,
    "summarization": SUMMARIZATION,
    "reasoning": REASONING,
}

# Ollama's JSON mode was tried for extraction and dropped: grammar-constrained decoding ran
# about five times slower per token on CPU, and the prompt alone already yields bare JSON.
JSON_MODE_TASKS: frozenset[str] = frozenset()

assert set(LABELS) <= set(CLASSIFICATION.split())


def build_prompt(case: dict[str, Any]) -> str:
    return TEMPLATES[case["task"]].format(text=case["input"], limit=SUMMARY_WORD_LIMIT)


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()[:16]
