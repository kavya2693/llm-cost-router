# ADR-0001: Verifier-gated cascade, with thresholds tuned on a dev split

Status: accepted

## Context

The service handles four kinds of request: field extraction from order emails, ticket
classification, shift-report summaries and short arithmetic word problems. Sending all of
them to the largest model is the safe default and the expensive one. Most of the volume is
extraction and classification, which a 3B model often gets right.

The question is how to decide, per request, whether the small model's answer can be kept.

## Options considered

1. **Static routing by task type.** Send extraction and classification to the small model,
   everything else to the large one. Cheap to build, but it ships every wrong small-model
   answer, and there is no signal when one is wrong.
2. **A learned router that predicts difficulty from the prompt** (the RouteLLM family).
   Needs preference or outcome data from many thousands of requests to train, and the
   prediction happens before any answer exists, so it cannot use what the small model
   actually said.
3. **Cascade with a cheap verifier.** Always try the small model first, check its answer
   with a deterministic check (schema, grounding in the input, format) and the model's own
   token probabilities, and pay for the large model only when the check fails.
4. **Self-consistency or LLM-as-judge on the small answer.** Each adds at least one more
   model call per request, which eats most of the saving it is meant to protect.

## Decision

Option 3. For each task type the policy has two settings: which small model goes first
(or none, meaning straight to the large model) and the confidence threshold below which the
small answer is escalated. Both are chosen on a 20-case-per-task dev split, picking the
cheapest setting that completes at least as many dev cases as the large model alone. The
remaining 120 cases are held out and every headline number is reported on them.

Ground truth comes from the generator, not from a model, so the verifier and the scorer are
separate: the router never sees the answer key.

## Consequences

- The cascade adds the small model's latency to every escalated request. That shows up in
  the p95 latency column rather than being averaged away.
- With 20 dev cases per task, a single case moves completion by five points, so the
  thresholds are coarse. The held-out split and a paired bootstrap interval are there to
  show how much of the result is noise.
- The price table is an assumption, stated in `config.py`. Measured CPU latency is
  reported alongside it as a second, independent view of cost.
- phi4 (14B) was left out of the arms: on a shared CPU it ran too slowly to cover 200 cases
  inside the time budget.
