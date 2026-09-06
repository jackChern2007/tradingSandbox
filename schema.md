# Log schemas

Every record in `forecasts.jsonl` is validated against this document by
`healthcheck.py` on every run (the tables below are mirrored in its
`BASE_REQUIRED` / `FORECAST_REQUIRED` maps). A missing field, a wrong type, or a
field that must not exist is a **halt**.

## `forecasts/forecasts.jsonl`

One JSON object per line, one line per market per cycle. **Append-only and
immutable: a row is never edited after it is written.** Flush and fsync per
line. Outcomes live in `resolutions.jsonl`, never here.

### Identity & provenance (required on every record)
| field | type | notes |
|---|---|---|
| `record_id` | str | uuid4 |
| `cycle_id` | str | ISO8601 of the scan cycle; non-decreasing through the file |
| `attempt` | int | **always 1.** Any other value = retry = scorer refuses the sample |
| `model` | str | e.g. `qwen3.5-local` |
| `model_sha256` | str | hash of the weights file; must equal the pin in `study_start.json` |
| `prompt_version` | str | `v3`; must equal `study_start.json`; changing it starts a new, non-poolable sample |

### Market (required on every record)
| field | type | notes |
|---|---|---|
| `market_id` | str | Polymarket condition id; join key to `resolutions.jsonl` |
| `market_slug` | str | |
| `question` | str | verbatim |
| `resolution_criteria` | str | verbatim — outcomes turn on wording |
| `category` | str | politics / sports / crypto / other |
| `close_time` | str | ISO8601 |
| `scanned` | bool | logged even when not forecast |
| `skip_reason` | str\|null | why skipped, if skipped. When null, every field below is required |

### Sampling parameters (required when forecast)
| field | type | notes |
|---|---|---|
| `ts_estimate` | str | ISO8601, when Call A returned; not earlier than `cycle_id` |
| `temperature` | float | pinned in `study_start.json` |
| `top_p` | float | pinned |
| `seed` | int | pinned |

### Retrieval (the confound — log it fully)
| field | type | notes |
|---|---|---|
| `context_path` | str | path under `forecasts/context/`, full text, never truncated |
| `retrieved_chars` | int | drift signal |
| `retrieval_sources` | list[str] | urls/ids |
| `retrieval_ts` | str | ISO8601 |

### The two probabilities
| field | type | notes |
|---|---|---|
| `agent_p_verbalized` | float\|null | **primary metric.** From Call A |
| `agent_p_logprob` | float\|null | secondary. Derived from Call B |
| `logprob_yes` | float\|null | raw, summed over the accepted YES variants |
| `logprob_no` | float\|null | raw, summed over the accepted NO variants |
| `logprob_mass` | float\|null | share of raw first-token mass on accepted tokens |
| `agent_base_rate` | float\|null | |
| `evidence_quality` | str\|null | strong / mixed / thin |
| `stale` | bool\|null | model's own staleness call |
| `injection_flag` | str\|null | verbatim suspicious text, else null |
| `agent_reasoning` | str\|null | |

### Parse outcome
| field | type | notes |
|---|---|---|
| `parse_ok` | bool | false = failure record, kept and counted, never retried |
| `parse_failure_reason` | str\|null | `malformed_json`, `missing_field`, `p_out_of_range`, `p_is_0_or_1`, `low_logprob_mass`, `timeout`, `refusal` |

### Market price (fetched, withheld, written after)
| field | type | notes |
|---|---|---|
| `market_p_at_estimate` | float | mid of bid/ask at `ts_estimate` |
| `market_bid` | float | |
| `market_ask` | float | |
| `market_volume_usd` | float | liquidity proxy |
| `price_withheld_from_agent` | bool | **must be true or the row is dropped** |

### Fields that must NOT exist in a forecast record
`outcome`, `resolved`, `ts_resolved` (outcomes are a separate file — D2) and
`intended_size_frac`, `declared_size_cap` (the sizing probe was cut — D3). Their
presence is a schema violation and a halt.

---

## `forecasts/resolutions.jsonl`

Append-only. One line per resolved market, written by the resolution step, not
by the forecasting harness. **Not read by anything except `score.py` (values)
and `healthcheck.py` (line count and `market_id` only).**

| field | type | notes |
|---|---|---|
| `market_id` | str | join key |
| `outcome` | 0\|1 | |
| `ts_resolved` | str | ISO8601 |
| `source` | str | where the resolution was read from |

A market may appear more than once only with the same outcome; conflicting
outcomes make every forecast of that market unscoreable.

---

## `study_start.json`

Written **once**, at launch, by `start_study.py`. The cold-start alarm is
measured from `study_start_ts`; the pins are what `healthcheck.py` compares
every record against.

| field | type | notes |
|---|---|---|
| `study_start_ts` | str | ISO8601 |
| `model_sha256` | str | |
| `prompt_version` | str | |
| `expected_interval_s` | int | seconds between cycles |
| `temperature`, `top_p`, `seed` | optional | checked against every record if present |
| `protocol_commit`, `protocol_tag` | str\|null | git state of this repo at launch |
| `note` | str\|null | |

---

## `health/health.jsonl`

Written by `healthcheck.py`. **Operational statistics only — no outcome values,
no scores.** A *count* of resolved rows is permitted: it says how far along the
study is, not which way it is going. This is what keeps the supervisor, and
Jack, blind.

| field | type | notes |
|---|---|---|
| `ts` | str | |
| `study_start_ts` | str\|null | from `study_start.json` |
| `seconds_since_study_start` | float\|null | |
| `cycles_total` | int | |
| `records_total` | int | forecast + skipped |
| `forecasts_total` | int | non-skipped |
| `records_last_cycle` | int | |
| `forecasts_last_complete_cycle` | int\|null | the last cycle may be in progress, so the one before it |
| `forecasts_per_cycle_median` | float\|null | trailing 20 completed cycles |
| `parse_ok_rate` | float | |
| `parse_failures_by_reason` | dict | |
| `anchor_guard_rate` | float | |
| `attempt_violations` | int | |
| `mean_retrieved_chars` | float | |
| `thin_evidence_rate` | float | |
| `mean_abs_p_minus_half` | float | |
| `p_exactly_half_rate` | float | |
| `mean_verbalized_minus_logprob` | float | |
| `injection_flags_total` | int | |
| `resolved_count` | int | line count of `resolutions.jsonl` — markets resolved |
| `resolved_forecasts` | int | parse-ok forecast rows whose market has a resolution — progress toward N |
| `model_sha256_ok` | bool | |
| `pin_violations` | int | prompt_version / temperature / top_p / seed drift |
| `schema_violations` | int | |
| `clock_violations` | int | |
| `malformed_lines` | int | unparseable lines other than the last |
| `partial_last_line` | bool | last line unparseable (write in progress) |
| `disk_free_gb` | float | |
| `seconds_since_last_cycle` | float\|null | |
| `active_reports` | list[str] | report-severity checks firing this sample; escalation is derived from three consecutive samples |
| `_expected_interval_s`, `_*_problems` | | diagnostics for the alarm text |

## `health/alarms.jsonl`

| field | type |
|---|---|
| `ts` | str |
| `severity` | `halt` \| `report` |
| `check` | str — see the invariant tables in `SUPERVISOR.md` |
| `detail` | str |
