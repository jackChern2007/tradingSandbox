# Pre-registration: is a local LLM's probability estimate better than the market price?

**Status:** committed before any data collection.
**Author:** Jack Cherniawski
**Drafted:** 2026-08-28 · **Amended:** 2026-09-06 (see §10 — all amendments before collection)
**Venue:** Polymarket (US, CFTC-regulated since Dec 2025; available in Missouri; 18+)
**Forecaster under test:** local Qwen3.5, weights pinned by SHA-256, fixed seed and sampling params, thinking mode off

Everything here is fixed *before* the first forecast is logged. Anything decided
after seeing data is exploratory and must be labelled as such in the writeup.
The point of writing this first is that it removes the option of choosing what
counts as a result once results exist.

---

## 0. What prompted this

A widely circulated claim: an agent turned $50 into ~$5,000 on Polymarket in 48
hours using Kelly sizing capped at 6% risk against 8% mispricings. Under those
stated parameters, 105x requires roughly 973 consecutive winning trades with
zero losses against at most 288 scan cycles. The claim is arithmetically
impossible on its own numbers, and the same text appears from multiple accounts
with different dollar figures.

That claim needs no experiment. The question underneath it does.

---

## 1. Questions, ordered by how cheaply they can be answered

**Q1 — Is the model calibrated?** When it says 70%, does it happen ~70% of the
time? *Well-powered at a few hundred resolved markets.*

**Q2 — Does it beat the market price?** Paired comparison on identical
questions. *Badly underpowered at any reachable N — see §5.*

**Q3 — Do its stated and internal probabilities agree?** Verbalized number
versus logprob-derived number, same question, same moment. *Cheap, and does not
require resolution at all.*

**Q4 — Can it be manipulated through the untrusted text it reads?** *Existence
proof. One clean instance is the finding.* (Whether it follows its own sizing
rules was cut from this study — §10, D3 — and gets its own prompt and its own
pre-registration later.)

Q1, Q3 and Q4 are the deliverable. Q2 is reported honestly and will almost
certainly be inconclusive. That is stated here, in advance, so an inconclusive
Q2 cannot later be presented as promising.

---

## 2. Hypotheses

- **H1 (primary, directional):** the model is overconfident — forecasts sharper
  than accuracy justifies. Predicted: negative reliability gaps at high p,
  positive at low p.
- **H2 (secondary):** the model's Brier score is *not* better than the market
  price. Stated as a null expected to survive, not as a hope.
- **H3:** verbalized and logprob-derived probabilities diverge systematically,
  with the verbalized number more clustered on round values (0.7, 0.75, 0.8).
- **H4 (exploratory):** instruction-shaped text placed in sources the model
  reads can alter its stated forecast or its intended position size.

---

## 3. Design

Paper only. No capital at risk. Funding is gated on §6 and the honest
expectation is that the gate never opens.

**3.1 The anchoring guard.** The model produces its probability *before* the
market price enters its context. This is architectural, not instructional: the
harness fetches the price, withholds it, collects the estimate, then writes both.
Every record carries `price_withheld_from_agent`; any record where this is not
`true` is dropped by the scorer and cannot be argued back in.

**3.2 Dual elicitation.** Every forecast produces two numbers from the same
context: a **verbalized** probability the model writes out, and a **logprob**
probability read from the relative likelihood of the YES/NO continuation tokens.
The verbalized number is the pre-committed primary — it is what an agent would
act on. The logprob number is a pre-registered secondary. Their divergence is Q3.

Thinking mode is **off for both calls** (`elicitation.md`). Call B cannot be
read at all with a think tag as the first token, and if only Call A thought, Q3
would measure reasoning mode rather than elicitation method. A thinking-on arm
is a separate pre-registration. Call B is constrained at the decoder to Yes/No
and its numbers are read from the raw next-token distribution; the accepted
token variants and the `logprob_mass` floor of 0.50 are fixed in
`elicitation.md` before collection.

**3.3 Fail closed. Never retry.** A malformed response, a timeout, a refusal or
an unparseable number is logged as a failure with its reason and the cycle moves
on. **No retries, no repairs, no reruns of the same market in the same cycle.**
Retrying until output parses deletes hard questions from the sample and biases
calibration upward invisibly. `attempt` is always 1; the scorer rejects anything
else. Parse-failure rate is a reported result, not an inconvenience.

**3.4 Sampling.** Markets are selected by a fixed mechanical rule written before
collection — not by the model's preference and not by the supervisor. The model
forecasts every market handed to it. Every market scanned is logged whether
forecast or skipped, so selection is visible in the data.

**3.5 Retrieval is a logged variable.** The full retrieved context is stored per
forecast, not a reference to it. A local model with a training cutoff is heavily
dependent on retrieval; a result here is a result about *this model plus this
retrieval pipeline*, and the logs must let a reader separate them.

**3.6 Blinding.** Outcomes are not read until the scoring run. The scorer runs
once at the pre-committed N, not repeatedly while data accumulates. Operational
monitoring during collection is outcome-blind by construction (§4).

Forecast records are immutable once written and never carry an outcome.
Resolutions go to a separate append-only file keyed by market id, and the scorer
joins the two. A *count* of resolved forecasts is visible during collection —
it is how anyone knows N has been reached — because a count carries no
direction. Outcome values do not leave the resolutions file until scoring.

**3.7 Public communication during collection.** A devlog is published while the
study runs (`DEVLOG.md`). Entries are **operational only** — throughput, parse
failures, retrieval health, verbalized/logprob divergence, injection flags, the
resolved count, and what broke. **No accuracy figure or outcome value appears
in any entry before N is reached.** This protects blinding, and it removes the
commitment cost of publicly abandoning a result later. Entries are generated from
`health/` via `devlog.py`, which cannot read outcomes.

**3.8 Determinism.** Weights hashed, temperature and seed and top_p fixed and
logged per record. A change to any of them starts a new sample that is not
poolable with what came before.

---

## 4. Supervisor boundary

A supervising agent (Claude) watches the run. Its access is limited by the
filesystem, not by policy.

**The rule:** *if the supervisor's output could change a logged probability, or
change which records exist, it is contamination.*

The second clause is the one that bites. Skipping a market, retrying a call, or
rewording a prompt mid-run alters the sample without ever producing a number.

| Supervisor MAY | Supervisor MAY NOT |
|---|---|
| read `health/` | read `forecasts/` during collection |
| report anomalies to Jack | fix, retry, or repair anything |
| **halt** the study | resume, patch, or adjust a running study |
| review harness code *before* launch | touch harness code during collection |
| score, analyse, write up *after* N | see any outcome before N |

The supervisor has exactly two actions: **report** and **halt**. There is no
third, because the third is always the one that contaminates.

`health/` is written by `healthcheck.py`, a trusted one-way valve run by Jack —
not by the supervisor. It emits **operational statistics only: no outcome
values, no scores.** It validates every record against `schema.md`, checks the
clock, and halts on a harness that never started. This preserves blinding for
the supervisor *and* for Jack, which matters more, since Jack is the one who
could rationalise an early peek.

The boundary is enforced by filesystem permissions and checked by
`verify_boundary.sh`, run by Jack as the supervisor user. The supervisor cannot
verify its own sandbox from inside it.

---

## 5. Power — the finding that reshaped this study

Simulated before collection (`power.py`), modelling market and model as true
probability plus Gaussian noise:

| model σ | market σ | Brier gap | N for 80% power |
|---|---|---|---|
| 0.02 | 0.06 | 0.0032 | ~3,000 |
| 0.03 | 0.06 | 0.0027 | ~6,000 |
| 0.04 | 0.06 | 0.0020 | ~12,000 |
| 0.02 | 0.10 | 0.0096 | ~800 |

**Detecting a genuine edge takes thousands of resolved markets.** A model three
times more accurate than the market still needs ~3,000 resolutions to prove it.
That is out of reach here, and it is recorded now rather than discovered later.

Detecting a *deficit* is cheap: a synthetic overconfident forecaster is caught
decisively at N=300.

**The asymmetry is the result, not a defect.** An edge too small to detect in a
few hundred markets is also too small to survive the spread — 2¢ on a 50¢
contract is ~4% round trip. "Undetectable at N=300" and "unprofitable" point the
same way.

**Committed N:** 300 resolved forecasts. No inference before; no continuation
past it hoping the sign flips.

---

## 6. Stop conditions, committed now

1. **CI excludes zero, favouring the market** → no edge. Ends. Do not fund.
2. **CI spans zero at N=300** → no detectable edge. Ends. Do not fund. Per §5
   this is the expected outcome and is a complete answer.
3. **CI excludes zero, favouring the model** → the only branch permitting
   discussion of a funded phase, and then only after a replication on a fresh
   300 markets. One p<0.05 among this many diagnostics is not a finding.
4. **Calendar stop:** 10 weeks from first forecast regardless of N.
5. **Integrity stop:** >5% of records failing the anchoring guard means the
   sample is contaminated. Discard and restart with a fixed harness.
6. **Parse-failure stop:** >25% parse failures means the elicitation prompt is
   mismatched to the model. Halt, fix, restart the sample — do not blend.
7. **Silent-failure stop:** any health alarm in §4 firing uninvestigated for
   more than 24h halts the run.

If a funded phase ever happens: quarter-Kelly, not full Kelly (Kelly is optimal
only if the probability is correct, and §5 says that will not have been
established); an amount whose total loss is irrelevant; a pre-set max drawdown.

---

## 7. Primary metric

Paired difference in Brier score, market minus model, using the **verbalized**
probability, on identical questions at identical timestamps, with a 95% paired
bootstrap CI (20,000 resamples). Positive means the model is better.

Committed in advance so it cannot be swapped. Logprob Brier, log loss, sharpness,
calibration tables, parse rates and any P&L figure are **diagnostic only**. A
favourable diagnostic beside a null primary is a null result.

---

## 8. What gets published either way

The writeup goes out whichever way it lands, including — especially — "the model
is worse than the price and I could not have proven otherwise at this sample
size." A null result that actually gets reported is the scarce thing in this
category. Raw logs published alongside.

---

## 9. Known limitations, stated before collection

- Single model, single prompt version, single retrieval pipeline. Not a claim
  about LLMs generally, and not a rebuttal of any claim made about a different
  model.
- The model has a training cutoff. Results are about model + retrieval jointly.
- Fast-resolving markets skew to sports and crypto — the most efficient and most
  bot-saturated. Slower markets are softer but resolve outside the window. Not
  resolvable within the study; it bounds what the result can mean.
- Paper forecasting ignores slippage, spread, partial fills and liquidity. A
  paper edge is a ceiling, never an estimate of realised return.
- Local inference on consumer hardware may constrain cycle rate; throughput is
  logged so it can be checked as a confound.
- Jack runs, scores and writes this up. Pre-registration plus the §4 boundary are
  the only guards against that, which is why they exist.

---

## 10. Amendment log

| Date | Change | Before collection? |
|---|---|---|
| 2026-08-28 | Forecaster changed from hosted frontier model to local Qwen3.5, pinned. Rationale: eliminates silent mid-study model updates, which would be an undetectable confound in a 10-week run. | Yes |
| 2026-08-28 | Added dual elicitation (verbalized + logprob) and Q3/H3. | Yes |
| 2026-08-28 | Added §3.3 fail-closed/no-retry and the parse-failure stop. | Yes |
| 2026-08-28 | Added §4 supervisor boundary and outcome-blind health logging. | Yes |
| 2026-08-28 | Added §3.7 public communication / devlog policy. | Yes |
| 2026-09-06 | **D1.** Thinking mode off for both elicitation calls; Call B constrained at the decoder, numbers read from the raw distribution; accepted-token set and `logprob_mass` floor (0.50) fixed. `prompt_version` v2 → v3. Rationale: a think tag as first token makes Call B unreadable, and mixed modes would make Q3 uninterpretable. | Yes |
| 2026-09-06 | **D2.** Resolutions moved out of the forecast record into a separate append-only `resolutions.jsonl` joined on `market_id`; forecast records immutable on write. A resolved *count* is now visible in `health/` (no direction). Rationale: removes the "append-only but we edit three fields" contradiction and the torn-write risk; lets a blind observer know when N is reached. | Yes |
| 2026-09-06 | **D3.** Sizing probe (`intended_size_frac`, `declared_size_cap`) and the rule-drift measurement cut; Q4 narrowed to injection. Rationale: Call A had no source for the fields, and asking the not-trading call for a stake reintroduces the betting frame into the primary measurement. Rule drift gets its own pre-registration. | Yes |
| 2026-09-06 | Instrument fixes, no protocol change: synthetic "null" fixture was market-plus-noise (strictly worse by construction) and is now independent same-σ noise around truth, with the old construction kept as `worse`; scorer refuses to score any sample containing a retry (exit 3) instead of warning and reporting; parse-failure denominator is every attempt rather than resolved rows only. | Yes |
| 2026-09-06 | Health invariants promised in §4 / `SUPERVISOR.md` implemented: cold start (needs `study_start.json`, written once by `start_study.py`), clock, full-record schema validation, config pins, forecasts-per-cycle drift, and escalation of any report sustained 3 consecutive health samples to halt. `verify_boundary.sh` added. | Yes |
| 2026-09-06 | *(after tag `prereg-v1`)* `selection_rule.md` and `retrieval.md` added as **DRAFTS**. Not in force: §3.4's "fixed mechanical rule" and §3.5's pipeline remain unspecified until Jack signs them, which will be a further row and a `prereg-v2` tag. No other file changed. | Yes |
| 2026-09-06 | *(after tag `prereg-v1`)* `tokencheck.py` added: the tool that runs the one-time tokenizer/decoder check prescribed in `elicitation.md` and prints its record table. Tooling only; the accepted-token policy and 0.50 floor are unchanged. | Yes |

No amendments after the first logged forecast. Any later change ends this
pre-registration and starts a new one.
