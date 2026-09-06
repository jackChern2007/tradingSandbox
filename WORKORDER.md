# Work order — pre-launch changes

For the coding agent. All changes are **pre-collection**; nothing is frozen yet.
Every item lands in the amendment table in `PREREGISTRATION.md` §10 with
"before collection? Yes".

---

## Decisions already taken (do not relitigate)

**D1. Thinking mode OFF for both calls.** Mandatory for Call B (the first token
would be a think tag and the Yes/No probe reads noise). Required for Call A too,
because if A thinks and B does not, Q3 measures *reasoning mode* rather than
*elicitation method* and the divergence result is uninterpretable. Thinking-on is
a separately pre-registered second arm, never a variation folded into this sample.

**D2. Resolutions live in a separate file.** `forecasts.jsonl` becomes immutable
on write. Resolution moves to append-only `resolutions.jsonl` keyed by
`market_id`, joined by the scorer. This removes the "append-only but we edit
three fields" contradiction and the torn-write risk.

**D3. Sizing fields are cut.** Call A tells the model it is not trading, and that
framing is load-bearing for an honest credence. Asking the same call for a stake
size reintroduces the betting frame and measurably shifts stated probability —
trading a confound in the *primary* measurement for a *secondary* probe. Rule
drift gets its own study with its own prompt, later.

If Jack disagrees with any of these, stop and raise it before starting.

---

## Already done (files supplied, tested — apply, don't rewrite)

- `make_synthetic.py` — true null is now independent noise of the same σ around
  the *true* probability, not market-plus-noise. New `worse` fixture carries the
  old (deliberately inferior) construction.
- `score.py` — refuses to score any sample containing a retry (exit 3);
  parse-failure denominator is now every forecast attempt, not just resolved rows.

---

## Tasks

### T1 — Schema split (D2)
- Remove `resolved`, `outcome`, `ts_resolved` from the forecast record.
- New `forecasts/resolutions.jsonl`: `{market_id, outcome (0|1), ts_resolved, source}`.
- `score.py` joins on `market_id`. A forecast with no matching resolution is
  simply unresolved — not an error.
- Update `schema.md`.
- **Accept:** `score.py` produces identical output on a fixture split across the
  two files as it does on the current single-file fixture.

### T2 — Drop sizing (D3)
Remove `intended_size_frac` and `declared_size_cap` from `schema.md`,
`healthcheck.py` (`size_cap_violations`), `devlog.py`, `score.py`, and the
`make_synthetic.py` fixtures. Remove the rule-drift paragraphs from `schema.md`
and `DEVLOG.md`.
- **Accept:** `grep -ri "size_cap\|intended_size" .` returns nothing outside
  `PREREGISTRATION.md` §10.

### T3 — Elicitation update (D1)
- State thinking-mode disabled for both calls, and how (stack-specific flag).
- Call B constrained **at the decoder** — GBNF grammar in llama.cpp, logit bias
  in vLLM — restricting the first token to `Yes`/`No`. Do not rely on the prompt.
- Record the accepted token set and the measured `logprob_mass` floor from the
  one-time tokenizer check.
- Bump `prompt_version` to `v3`.

### T4 — Unblock the resolved count
Blinding covers outcome *values*, not sample *size*. A count of resolved rows
carries no information about direction.
- `healthcheck.py`: add `resolved_count` (integer only, from `resolutions.jsonl`
  line count — never read the outcome field).
- Narrow `FORBIDDEN` in both `healthcheck.py` and `devlog.py` so it blocks
  outcome values and scores but permits `resolved_count`.
- `devlog.py`: surface `resolved_count` and progress toward 300.
- **Accept:** health output contains a count and still contains no outcome,
  no Brier, no accuracy.

### T5 — Missing health invariants
`SUPERVISOR.md` promises these; `healthcheck.py` implements none.
- **Clock:** halt if `seconds_since_last_cycle` is negative, or if cycle
  timestamps are non-monotonic, or skew > 60s.
- **Cold start:** halt if `forecasts.jsonl` is missing or empty more than
  3× the expected interval after study start. Requires a `study_start_ts`
  written once at launch — currently no timestamp exists, so the alarm cannot
  fire. This is the worst case (harness never started) and is currently silent.
- **Schema validation:** halt on any record missing a required field or with a
  wrong type. Validate every record, not a sample.
- **Forecasts-per-cycle drift:** report if `records_last_cycle` changes without a
  config change.
- **Sustained escalation:** any `report` signal firing on 3 consecutive health
  samples escalates to `halt`. Write `active_reports` into each health record so
  escalation is derivable from history rather than held in memory.
- **Accept:** a fixture with a future timestamp halts; a fixture with an empty
  forecasts file past the grace window halts; a drift signal repeated 3× halts.

### T6 — Boundary verification
New `verify_boundary.sh`, run as the supervisor user. It must **assert failure**:
`cat study/forecasts/forecasts.jsonl` returns EACCES. Exit non-zero if the read
succeeds. Add to the README pre-launch checklist.

Add to `SUPERVISOR.md` as a stated limitation: whoever writes the supervisor
cannot verify its own sandboxing from inside; Jack runs this check.

### T7 — Amendment rows
One row per decision D1/D2/D3 and one for the code fixes, all dated, all
"before collection? Yes".

### T8 — Commit and tag
`git init`, commit everything, tag `prereg-v1`. Then publish the commit hash
somewhere with an independent clock — a public repo or the first devlog post.
A local git timestamp is forgeable; an externally witnessed hash is not.
**Do this after T1–T7, so the tag marks the frozen protocol.**

---

## Then, and only then: the two missing specs

Neither exists, and the harness cannot be correctly built without them. These are
design decisions for Jack, not code — the agent may draft, Jack decides.

**`selection_rule.md`** — how markets are chosen each cycle. Must be mechanical
and reproducible: category filter, resolution-window bounds, minimum liquidity,
how many per cycle, ordering, and what happens on ties. The pre-registration
already claims this is fixed before collection.

**`retrieval.md`** — what the model is given as evidence. Sources, tool, query
construction, recency window, token budget, and what happens when retrieval
returns nothing. A result here is a result about *model plus this pipeline*;
without the spec, nobody can tell which half produced it.

---

## Harness invariants — non-negotiable

The harness does not exist yet. When it is built, these must hold. They are the
acceptance criteria, not suggestions.

1. **The price is fetched after the estimate is collected**, or fetched earlier
   and held in a variable that never enters any prompt string. There must be no
   code path where a price reaches model context. This is the study.
2. **No retries. Anywhere.** Not in the HTTP client, not in a `for attempt in
   range(3)`, not in an exception handler that re-enters the call. Disable
   library-level retry explicitly (`urllib3` `Retry(total=0)`, or equivalent).
   `attempt` is written as 1 and never incremented.
3. **A failure writes a record.** Timeout, refusal, malformed output — all get a
   row with `parse_ok: false` and a reason. Never a silent skip.
4. **Selection cannot see model output.** The market list for a cycle is fixed
   before any inference runs in that cycle.
5. **Write once, append only.** No record is rewritten. Flush and fsync per line.
6. **Provenance is recorded, not defaulted.** `model_sha256`, `seed`,
   `temperature`, `top_p`, `prompt_version` come from the running config, never
   from a hardcoded fallback.
7. **Full retrieved context is stored**, not a reference or a truncation.

---

## Things not to do

- Do not add retry logic anywhere, for any reason, however reasonable it seems.
  It is the single change that invalidates the study.
- Do not relax the scorer's gates to get a sample to score.
- Do not "improve" `PREREGISTRATION.md` §5 (power). The numbers are pessimistic
  on purpose and they are the honest result.
- Do not make the supervisor able to fix things. Report and halt, nothing else.
- Do not put any accuracy figure in a devlog entry before N.
- Do not touch anything after the `prereg-v1` tag without a new amendment row —
  and after the first logged forecast, amendments end.
