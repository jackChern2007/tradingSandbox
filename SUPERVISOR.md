# Supervisor operating contract

The supervising agent (Claude) watches a run it must not influence.

## The rule

> If the supervisor's output could change a logged probability, or change
> **which records exist**, it is contamination.

The second clause is the one that gets violated. Skipping a market, retrying a
failed call, or rewording a prompt mid-run alters the sample without the
supervisor ever producing a number. Selective intervention is worse than random
intervention, because "something went wrong" correlates with *hard questions* —
so a helpful supervisor quietly deletes the hard cases and leaves the forecaster
looking better calibrated than it is, invisibly and unrecoverably.

## Enforce it with the filesystem, not with discipline

```
study/
  study_start.json    # written once at launch; start timestamp and pins
  forecasts/          # supervisor has NO read access during collection
    forecasts.jsonl   # immutable forecast records
    resolutions.jsonl # outcomes, keyed by market_id
    context/          # retrieved-context blobs, one per forecast
  health/             # supervisor reads ONLY this
    health.jsonl      # operational stats, appended each cycle
    alarms.jsonl      # threshold breaches
  devlog/
    entries/          # append-only record, written by the supervisor
    posts/            # publication drafts, freely edited
```

The scripts (`healthcheck.py`, `devlog.py`, `score.py`, `start_study.py`,
`verify_boundary.sh`) live in this repo, outside `study/`.

Procedural rules erode at 2am in week six. Permissions don't. Give the
supervisor process read access to `health/` and nothing else.

`healthcheck.py` is the only thing that reads forecasts during collection, and
it emits **operational statistics only — no outcome values, no scores.** A
*count* of resolved forecasts is included: blinding covers outcome values, not
sample size, and without the count nobody blind could tell when N was reached.
That keeps the supervisor blind, and more importantly keeps *Jack* blind, since
he is the one who could rationalise an early peek.

### Verifying the boundary — and who can

`verify_boundary.sh` asserts *failure*: run as the supervisor user, every read
under `forecasts/` must be refused with `Permission denied`, and it exits
non-zero if any read succeeds. **Jack runs it, before launch and after any
permission change.**

Stated limitation: whoever writes the supervisor cannot verify its own
sandboxing from inside. A process that can read `forecasts/` and is asked "can
you read `forecasts/`?" has already answered the wrong way, and one that cannot
has no way to prove it is not lying. The check is only meaningful when run by a
human from outside the supervisor's context.

## The supervisor has three actions

**REPORT** — summarise health, name the anomaly, say what it probably means.

**HALT** — stop the run and notify Jack.

**WRITE A DEVLOG ENTRY** — see `DEVLOG.md`. Operational only, built from a
`devlog.py` digest, no outcome data before N. Writing to `devlog/entries/`
touches nothing the study reads, so it cannot bias the sample.

There is no fourth action. The third action is always the one that contaminates.
When something breaks, the supervisor does not fix it. It halts and reports, and
a human decides whether the sample survives or restarts.

## What the supervisor is actually for

The dangerous failure in a long unattended run is not a crash. A crash is loud.
The dangerous one is a harness that **keeps running while producing garbage** —
the retrieval pipeline starts returning empty context, nothing errors, and the
model forecasts from priors alone for three weeks. Every record is well-formed.
The study is dead and nothing said so.

Catching that is the job, and all of it lives in `health/`.

### Mechanical invariants — halt on any breach

All implemented in `healthcheck.py`; the `check` name is what appears in
`alarms.jsonl`.

| Check | `check` | Halt condition |
|---|---|---|
| study started | `study_start_missing` | no `study_start.json` — liveness cannot be judged |
| cold start | `cold_start` | no forecast written within 3× interval of `study_start_ts` — the harness never started, the worst silent failure |
| harness alive | `harness_alive` | no new cycle in 3× expected interval |
| model integrity | `model_integrity` | `model_sha256` differs from the pinned value |
| config pins | `config_pin`, `config_mismatch` | `prompt_version` / `temperature` / `top_p` / `seed` differ from `study_start.json`; or CLI pins disagree with it |
| anchoring guard | `anchoring_guard` | `price_withheld_from_agent` rate < 100% |
| retry discipline | `retry_discipline` | any record with `attempt` != 1 |
| clock | `clock` | any `cycle_id` earlier than a previous one; any `cycle_id` or `ts_estimate` more than 60s in the future; `ts_estimate` more than 60s before its `cycle_id` |
| schema | `schema` | any record missing a required field, with a wrong type, carrying an outcome or sizing field, or any unparseable line other than the last |
| disk | `disk` | free space < 2 GB |
| parse failures | `parse_failures` | cumulative parse-ok rate < 75% — §6.6 stop |

### Drift signals — report, and halt if sustained 3 consecutive health samples

| Signal | `check` | Meaning |
|---|---|---|
| mean `retrieved_chars` drops > 50% vs trailing 20-sample median | `retrieval_drift` | retrieval broke silently |
| `evidence_quality: thin` share rises > 30 pts | `evidence_quality_drift` | retrieval broke, or market mix shifted |
| mean \|p − 0.5\| < 0.05 | `no_discrimination` | model has stopped discriminating |
| verbalized/logprob mean gap > 0.10 | `elicitation_divergence` | elicitation or tokenization broke |
| forecasts in last complete cycle differ > 50% from trailing median | `selection_drift` | selection rule drifting (config is pinned for the run, so any change is drift) |
| p exactly 0.5 share > 20% | `refusal_to_commit` | model refusing to commit; check refusals |
| last line of `forecasts.jsonl` unparseable | `partial_write` | a write in progress, or a torn write |

Each health record carries `active_reports`, the list of report-level checks
firing in that sample. When the same check appears in three consecutive
samples the third is written as a **halt**. Escalation is derived from
`health.jsonl`, not held in memory, so a restarted health check does not forget.
Schedule the health check at the cycle interval so "three samples" means "three
cycles".

These are cheap to compute and none require an outcome.

## Before launch — the highest-value use of the supervisor

Adversarial review of the harness code, looking specifically for:

1. Any path where the price could reach the model's context.
2. Any implicit retry — library-level, HTTP-level, or a loop that re-enters on
   exception.
3. Any place a forecast is written twice, or not written on failure.
4. Whether `attempt`, `model_sha256`, `seed`, and the full retrieved context are
   genuinely recorded rather than defaulted.
5. Whether the market-selection rule can be influenced by model output.
6. Whether a failure of any kind (timeout, refusal, exception) still writes a
   record, and whether any code path writes a record twice.

Then `python3 test_instruments.py` must be green, and Jack must have run
`verify_boundary.sh` as the supervisor user.

Once collection starts, the cheapest supervisor is the one that mostly does
nothing.

## After N is reached

The boundary lifts. Scoring, analysis, adversarial review of the scorer, and the
writeup are unrestricted — the sample is closed and can no longer be biased.
