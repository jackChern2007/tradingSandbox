# Agent vs. market: a calibration study on Polymarket

Is a local LLM's probability estimate better calibrated than the market price?

Forecaster under test: **local Qwen3.5**, weights pinned by SHA-256, fixed seed
and sampling parameters, thinking mode off. Paper only — no capital at risk.

## Read in this order

1. **`PREREGISTRATION.md`** — the committed protocol. Change anything you want
   changed *now*; after the first logged forecast every change is exploratory and
   must be labelled that way.
2. **`SUPERVISOR.md`** — what the supervising agent may and may not touch, and
   why the boundary is enforced with filesystem permissions rather than good
   intentions.
3. **`elicitation.md`** — the two calls per market. Price-blind by construction.
4. **`schema.md`** — forecast log, resolutions, start file, health log.
5. **`DEVLOG.md`** — how progress gets written up while the study is still
   blind, and why the obvious number is missing from every entry.
6. **`selection_rule.md`** and **`retrieval.md`** — the two specs the harness is
   built against. Drafts until Jack signs them.

## Code

| file | when | what |
|---|---|---|
| `make_synthetic.py` | before launch | fixtures with known ground truth |
| `power.py` | before launch | the power simulation behind §5 |
| `test_instruments.py` | before launch, and after any change | acceptance tests for everything below |
| `verify_boundary.sh` | before launch, run by Jack **as the supervisor user** | proves the supervisor cannot read `forecasts/` |
| `start_study.py` | **once**, at launch | writes `study_start.json`: start timestamp and pins |
| `healthcheck.py` | every cycle | one-way valve: forecasts → health stats; halts on any invariant breach |
| `devlog.py` | weekly | factual digest from `health/` for a devlog entry |
| `score.py` | **once, after N=300** | the primary metric and diagnostics |

Stdlib only. No dependencies.

## Layout

```
study/                      # data root; not in this repo
  study_start.json          # written once by start_study.py
  forecasts/                # supervisor has NO read access during collection
    forecasts.jsonl         # immutable, append-only forecast records
    resolutions.jsonl       # append-only outcomes, keyed by market_id
    context/                # full retrieved text, one file per forecast
  health/                   # supervisor reads ONLY this
    health.jsonl
    alarms.jsonl
  devlog/
    entries/                # append-only record
    posts/                  # publication drafts
```

## Order of operations

1. Read the pre-registration. Edit freely. Then stop editing.
2. **Validate the instrument before it touches real data:**
   ```
   python3 test_instruments.py                          # all green, or stop
   python3 make_synthetic.py
   python3 score.py syn_null.jsonl     --min-n 300      # must say CI spans zero
   python3 score.py syn_worse.jsonl    --min-n 300      # must say market beats model
   python3 score.py syn_overconf.jsonl --min-n 300      # must say market beats model
   python3 score.py syn_retry.jsonl    --min-n 300      # must refuse, exit 3
   ```
3. Run the one-time tokenizer/decoder check in `elicitation.md` and fill in its
   table.
4. Set filesystem permissions, then have Jack run the boundary check as the
   supervisor user. It must print `BOUNDARY HOLDS`:
   ```
   sudo -u <supervisor-user> ./verify_boundary.sh study
   ```
5. Launch:
   ```
   python3 start_study.py --root study --pin <sha256> --prompt-version v3 \
       --interval <seconds> --temperature <t> --top-p <p> --seed <s>
   ```
   Then start the harness, and schedule `healthcheck.py --root study` at the
   cycle interval. Non-zero exit means halt.
6. Collect. **Do not score mid-collection.**
7. Score once at N=300 resolved forecasts (`resolved_forecasts` in health).
   Publish either way.

## The four rules that matter

**The probability is produced before the price enters context.** If that ordering
breaks, the study measures nothing.

**Fail closed, never retry.** A parse failure is a result. Retrying until output
parses deletes hard questions from the sample and biases calibration upward
invisibly.

**The supervisor reports or halts. It never fixes.** Selective intervention
correlates with question difficulty, which is the same bias wearing a helpful
face.

**The devlog never carries an accuracy figure before N.** Saying publicly at week
three that it looks good makes abandoning it at week ten expensive.
Pre-registration exists to keep that decision free.
