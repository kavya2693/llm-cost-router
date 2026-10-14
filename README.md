# llm-cost-router

[![ci](https://github.com/kavya2693/llm-cost-router/actions/workflows/ci.yml/badge.svg)](https://github.com/kavya2693/llm-cost-router/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.11-blue)
![license](https://img.shields.io/badge/license-MIT-green)

Send each LLM request to the cheapest self-hosted model likely to get it right, check the
answer with a cheap verifier, and pay for the large model only when the check fails. Measured
on a 200-case suite with known answers, against sending everything to the large or the small model.

## The problem

An operations platform pushes about sixteen million LLM requests a year through self-hosted
models: pulling order fields out of customer emails, sorting support tickets into queues,
summarising warehouse shift reports, and working short arithmetic questions for planners. The
safe default is to send all of it to the biggest model the team runs, because a wrong order
quantity or a ticket in the wrong queue costs a person's time to find and fix. That default is
also where most of the inference bill goes, and most of those requests are routine: a 3B model
reads "PO-91039, 2099 units, ship to Riyadh" just as well as a 7B one.

The team wants to stop paying large-model prices for routine work without the completion rate
dropping, and it needs to know which task types that is actually safe for.

## Why it is hard

Picking a cheap model per task type is easy. Knowing, per request, when the cheap model got it
wrong is not, because at serving time there is no answer key. A router that keeps every small
answer saves the most money and silently ships every mistake. The only signal available is what
can be checked cheaply: whether the output is well formed, whether its values appear in the
request, and how confident the model was in its own tokens. Whether those signals separate right
answers from wrong ones is an empirical question, and it has a different answer per task.

## The approach

A cascade ([ADR-0001](docs/adr/0001-verifier-gated-cascade-tuned-on-dev.md)). Each request goes
first to a small model (Qwen2.5-Coder 3B or Llama 3.2 3B), its answer goes through a task-specific
verifier, and only a failed check pays for Qwen2.5-Coder 7B. The verifiers are deterministic:
extraction must be JSON with the right keys and every value found in the email; a classification
must name exactly one known label; a summary must stay under 40 words and use only numbers from
the report; a reasoning answer must end on an `ANSWER:` line. On top of that each verifier reads a
confidence from the model's token log-probabilities.

The key decision is that the policy is tuned, not hand-set. For each task type, the dev split (20
cases per type) picks which small model goes first, or none, and the confidence threshold, choosing
the cheapest setting that completes at least as many dev cases as the large model alone. Every
headline number below is on the 120 held-out cases. Ground truth comes from the generator, so no
model ever judges another model's answer.

## The result

Held-out split, 120 cases (30 per task type). Cost uses the stated price table in
[`config.py`](src/llm_cost_router/config.py): $0.06 per million tokens for the 3B models and $0.20
for the 7B model. Latency is measured CPU inference time per request on an M1 Pro laptop. Full
output: [`outputs/results.md`](outputs/results.md), run log: [`outputs/run_log.txt`](outputs/run_log.txt).

| Arm | Completion | Hard cases | Cost / 1k req | Cost saved | p50 s | p95 s | Answered by small | Wrong answers let through |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| all-large (Qwen2.5-Coder 7B) | 85.0% | 75.0% | $0.0452 | - | 5.8 | 12.6 | 0% | - |
| all-small (Llama 3.2 3B) | 80.0% | 70.0% | $0.0125 | 72% | 3.4 | 8.5 | 100% | - |
| all-small (Qwen2.5-Coder 3B) | 75.8% | 60.0% | $0.0134 | 70% | 4.2 | 8.7 | 100% | - |
| router, structural check only | 77.5% | 63.3% | $0.0152 | 66% | 4.3 | 9.4 | 96% | 27 |
| **router, tuned on dev** | **82.5%** | 71.7% | **$0.0176** | **61%** | 3.6 | 20.4 | 91% | 16 |
| oracle (perfect verifier) | 91.7% | 85.0% | $0.0178 | 61% | 4.2 | 10.9 | 88% | - |

Router minus all-large completion, paired bootstrap 95% interval: **[-7.5, +2.5] points**. At the
logged traffic volume (16.4 million requests a year) the projected bill falls from $779 to $247.

**The router did not hold completion steady; it lost 2.5 points, and all of the loss is in
reasoning.** Per task type, completion (cost per 1k requests):

| Arm | extraction | classification | summarization | reasoning |
|---|---|---|---|---|
| all-large | 93% ($0.062) | 87% ($0.034) | 83% ($0.039) | 77% ($0.047) |
| router, tuned on dev | 93% ($0.016) | 87% ($0.010) | 87% ($0.012) | **63%** ($0.033) |
| oracle (perfect verifier) | 93% ($0.021) | 87% ($0.013) | 97% ($0.014) | 90% ($0.022) |

What this says:

1. **Extraction, classification and summarization are safe to route.** The router matches or beats
   the large model on all three at a quarter to a third of the cost, and the policy tuned on dev sends
   them to the small model with no confidence threshold at all: the structural checks were enough.
2. **Reasoning is not, and the verifier is why.** On dev the threshold (accept a 3B answer only if
   its answer digits have probability above 0.9999) matched the large model exactly, 95% vs 95%. On
   the held-out cases the 3B model was wrong six times with that much confidence, for example:

   ```
   1. Calculate the cost of the chairs: 38 * 168 = 6264 AED
   2. Add the delivery fee: 6264 + 45 = 6309 AED
   ANSWER: 6309          (correct: 6429)
   ```

   The confidence is read at the final answer, which only copies a number the model already wrote,
   so it measures copying, not arithmetic. The honest policy for this suite is to send reasoning
   straight to the large model; I have not switched it, because that decision would come from the
   test split.
3. **The oracle shows where the money is.** A perfect verifier at the same cost would beat the large
   model by about seven points, because the small models get right some cases the 7B gets wrong. The
   gap between 82.5% and 91.7% is entirely verifier quality.
4. **Escalation costs latency in the tail.** The router's p50 is lower than the large model's, but
   its p95 is 20.4 s against 12.6 s, because an escalated request waits for the small attempt and
   then the large one.
5. **Tuning per task is worth having.** One small model for everything with only the structural
   checks lets 27 wrong answers through; the dev-tuned choice of model per task, plus the reasoning
   threshold, cuts that to 16.

On all 200 cases (dev included) the tuned router reaches 87.0% against 88.0% for the large model
at 59% lower cost, interval [-4.5, +2.5] points; the dev cases flatter it.

**Inference time.** Running every model on all 200 cases took 13.3 min (Qwen 3B), 11.8 min
(Llama 3.2) and 20.7 min (Qwen 7B), 45.8 minutes in all on CPU: just over the 45-minute budget
I set. phi4 (14B) was dropped as the large arm for this reason.

## How it works

```
request ──► small model (per-task choice) ──► verifier ──► pass & confidence ≥ threshold ──► answer
                                                  │
                                                  └── fail ──► Qwen2.5-Coder 7B ──► answer

dev split (80 cases)   ──► calibrate(): per task, cheapest {model, threshold} with completion ≥ large
test split (120 cases) ──► every arm replayed from outputs/responses/ ──► graded against the answer key
```

- `data.py` writes the 200 cases with their answers, plus a year of daily request volume per task.
  Hard cases carry traps: a second order number or quantity in the email, a ticket that mentions
  two topics, a shift report with a distracting second count, multi-step arithmetic with an
  irrelevant number.
- `prompts.py` holds one prompt per task, identical for every model, so arms differ only by model.
- `client.py` calls Ollama at temperature 0 and appends every response, with token log-probabilities,
  to `outputs/responses/<model>.jsonl`. A changed prompt invalidates its cached answer.
- `verifiers.py` is what the router sees; `grading.py` is the answer key it never sees.
- `router.py` is the cascade; `evaluate.py` tunes it on dev, runs every arm and the bootstrap.
- Stop condition: at most two calls per request, one small and one large.

## Running it

```bash
uv sync
make check     # ruff, format, mypy, pytest
make run       # regenerate the suite and replay all arms from the cache, under a second
make models    # call the models through Ollama to refresh the cache; ~46 min on CPU
docker build -t llm-cost-router . && docker run --rm llm-cost-router
```

`make run` and the Docker image need no model server or key: they replay the committed responses.
`make models` needs a local Ollama with `qwen2.5-coder:3b`, `qwen2.5-coder:7b` and `llama3.2`
pulled. On macOS 13, set `OLLAMA_NUM_GPU=0`; the Metal path crashes there.

## The data

The dataset is generated by the seeded script `scripts/generate_data.py` (`make data`) and sized to
the production problem: 200 evaluation cases, 50 per task type split evenly into easy and hard, and
365 days of request volume per task type (about 16.4 million requests a year). The production data
is proprietary.

Each answer is fixed before the text is written: the generator picks the order fields, the ticket
label, the shift facts or the arithmetic, then writes the email, ticket, report or problem around
them, with typos, distractor numbers and second topics in the hard half. The traffic log has
weekday shape, holiday peaks, incident spikes and logging gaps, which the projection fills from the
same weekday nearby.

## Tests

`make check` runs 14 tests, all with stubbed models. The two that matter most:

- `test_confidence_threshold_catches_what_the_schema_check_cannot` gives the router a small model
  that returns well-formed but wrong answers with low token probability, and checks that only the
  threshold, not the structural check, escalates them. That is the mechanism the reasoning result
  above shows failing on real outputs, where wrong answers came with high probability.
- `test_calibration_never_trades_completion_for_cost_on_dev` checks that the tuned policy never
  completes fewer dev cases than the large model, whatever it saves.

The grader was written and tested against deliberately broken outputs first (a distractor
quantity, a wrong null date, two labels, an over-long summary, an earlier `ANSWER:` line), because
every number depends on it. CI also regenerates the suite and replays the cache, and fails if
`outputs/results.md` changes.

## Known limitations

- **Biggest gap: the verifier for reasoning does not work.** Token probability at the answer
  cannot tell a confident wrong calculation from a right one. I tried re-checking the arithmetic
  written in the working against the cache; it catches only the pure multiplication slips, a small
  share of the errors, since most are in setting the problem up. A real fix is a second, different
  signal (a program-of-thought re-computation, or agreement between the two small models), each
  of which costs another call.
- 20 dev cases per task is thin. One case is five points, the 7B model itself scored 95% on dev
  reasoning and 77% held out, and the bootstrap interval on the router's deficit is ten points wide.
- The price table is an assumption, not a quote; self-hosted models have no list price. The saving
  is driven by the 3.3x price ratio between tiers. The measured CPU latencies are the independent
  check on cost, and they point the same way at the median and the other way in the tail.
- CPU inference with several threads is not bit-for-bit deterministic even at temperature 0. A
  second pass of the 3B model on this machine changed a handful of answers. The cache is what makes
  the README reproducible, not the models.
- Latency was measured on a laptop shared with other jobs; absolute seconds will differ elsewhere.
- Four task shapes, one prompt each, no retries with a better prompt before escalating, and only
  the 3B/7B size classes.

## Insights and learning

I picked this because "use the small model for easy requests" is advice everyone gives and almost
nobody measures per task with a real answer key. The part I cared about was the verifier, since a
router is only as good as its ability to notice its own mistakes.

The surprise was how cleanly the result split. On extraction, classification and summarization the
plain structural checks did all the work, and a confidence threshold added nothing. On reasoning
the confidence threshold looked perfect on dev and failed held out, and the reason was obvious once
I read the cached answers: the model computes a wrong product in its working and then copies it
into the answer line with near-certainty. I had been treating "confident at the answer" as
"confident in the answer".

What I got wrong at first: the first reasoning prompt let the coder model answer in Python or
LaTeX, and the grader marked correct `\boxed{}` answers as missing. I fixed the prompt to ask for
plain-text working and taught the grader the boxed form before collecting the final cache. I also
started with Ollama's JSON mode for extraction and dropped it: grammar-constrained decoding ran
about five times slower per token on CPU, and the prompt alone already produced bare JSON.

Next: send reasoning to the large model, then try agreement between the two small models as the
reasoning verifier, which costs one extra cheap call but uses a signal that does not come from the
same model's own working.

## License

MIT
