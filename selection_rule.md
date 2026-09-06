# Market selection rule

**Status: DRAFT — a design proposal for Jack, not yet in force.** The
pre-registration (§3.4) says the rule is fixed mechanically before collection;
until this file is signed, that claim is unmet. Signing it is an amendment row
and a `prereg-v2` tag. Items marked ▶ are the decisions that need a human.

The rule has one job: choose markets each cycle with **no discretion** — not
the model's, not the supervisor's, not Jack's on a Tuesday. Given the same API
responses it must produce the same list, and every market it looked at must
leave a trace.

---

## 1. Universe

Each cycle, fetch every **active, not-closed, binary (exactly two outcomes,
Yes/No)** market from the Polymarket Gamma API, paginating to the end. Log the
universe size in the cycle's context header. Multi-outcome events are
represented as several binary markets; they enter the universe individually
(see 2.4 for the correlation cap).

*Field names (`end_date`, `volume24hr`, `event_id`, tags, best bid/ask) are to
be pinned against the live API during the harness build and written into this
file before signing.*

## 2. Eligibility — every filter mechanical, every failure logged

A market must pass all of the following. The **first** failing filter is its
`skip_reason`; it is written as a `scanned: true` record with base fields only.

| # | filter | value | why |
|---|---|---|---|
| 2.1 | resolution window | `end_date` in **[now + 24h, now + 21d]** | must resolve inside the 10-week calendar stop with margin; < 24h to close means the price is already ~0/1 and the question is over |
| 2.2 | liquidity | 24h volume ≥ **$5,000**, and an order book with both sides present and **spread ≤ 5¢** | below this the "market price" is one person's limit order, not a market |
| 2.3 | ▶ price band | mid in **[0.05, 0.95]** | excludes near-certain markets whose Brier contribution is ~0 either way. **This is a population-level restriction using the price** — it never touches a per-market prompt, but it does shape the sample toward uncertain questions, and it must be disclosed as such. Alternative: no band, and accept a tail of 2¢ markets |
| 2.4 | one per event | at most **one** market per `event_id` per cycle (the highest 24h volume) | siblings in a multi-outcome event are mechanically correlated; N=300 should not be 60 events × 5 |
| 2.5 | ▶ once only | a `market_id` is forecast **at most once in the whole study** | re-forecasting the same market at later times gives correlated rows and inflates N. Alternative: allow re-forecast after 7 days and dedupe at scoring |
| 2.6 | text sanity | question ≤ 400 chars, resolution criteria present and non-empty | small model, small prompt; a market with no criteria cannot be forecast honestly |

Filters run in this order, on data already fetched. The price read for 2.2 and
2.3 lives in a local variable that is **never** interpolated into any prompt
string (harness invariant 1); the price written to the record is re-fetched at
`ts_estimate`.

## 3. Ordering and ties — deterministic

1. Map each eligible market to a category: `politics`, `sports`, `crypto`,
   `other`, from the Gamma tags by a fixed lookup table kept in the harness
   config (any tag not in the table → `other`).
2. Within each category sort by 24h volume **descending**, ties by `market_id`
   **ascending** (lexical).
3. Round-robin across categories in the fixed order
   `politics, sports, crypto, other`, **starting at position
   `cycle_index mod 4`** so no category always gets first pick. Empty
   categories are skipped.
4. Take the first **K** (▶ 4). Everything eligible but not taken is logged
   with `skip_reason: "not_selected_rank_<n>"`.

## 4. ▶ Cadence, K, and budget

Targets: 300 resolved, parseable forecasts inside 10 weeks, allowing ~10%
parse failures and ~15% of markets that void, cancel, or slip past the window.

| parameter | proposed | reasoning |
|---|---|---|
| cycle interval | **6 hours** | 4 cycles/day; long enough for a consumer GPU to finish two calls × K markets with retrieval |
| K per cycle | **3** | 12 forecasts/day, ~84/week |
| forecast budget | **stop issuing new forecasts at 420** | 300 / (0.9 parse × 0.8 resolve) ≈ 417. Without a cap the sample keeps growing after N is reachable, which is the "continuation" §5 forbids |
| scoring trigger | `resolved_forecasts ≥ 300` in `health/`, or the calendar stop | whichever first |

At 12/day the budget is reached in ~5 weeks, leaving 5 weeks for the last
forecasts to resolve inside their 21-day window.

## 5. Order of operations inside a cycle — invariant 4

```
fetch universe → filter → order → select K
write every scanned record (the skips)            ← list is now frozen
for each selected market, in order:
    retrieve context (retrieval.md) → write context file
    Call A → Call B
    fetch price at ts_estimate
    write ONE forecast record (parse_ok true or false), flush, fsync
```

The selected list is written to disk **before** the first inference call of
the cycle. Nothing the model emits can add, remove, or reorder a market in the
cycle it runs in, or in any later cycle (selection reads only the API and the
set of `market_id`s already forecast).

## 6. Resolution step — a separate process

A small `resolve.py` (not yet written) runs **daily**, independent of the
harness:

- for every forecast `market_id` with no line yet in `resolutions.jsonl`, query
  Gamma; if the market has resolved to YES or NO, append
  `{market_id, outcome, ts_resolved, source}` with `source` the API endpoint
  and the resolution field read;
- ▶ voided / cancelled / 50-50 markets are written to a **separate**
  `forecasts/voided.jsonl`, not to `resolutions.jsonl`, so the resolved count
  in `health/` stays a count of scoreable rows. Alternative: write them to
  `resolutions.jsonl` with `outcome: null`, which the scorer already rejects
  but which inflates the progress count;
- it never reads `forecasts.jsonl` beyond the `market_id` column, never
  writes to it, and never runs inside the harness process.

## 7. What this rule cannot fix, stated now

- Markets that resolve inside 21 days skew to sports and crypto (§9 of the
  pre-registration). The round-robin softens the mix; it does not make
  politics markets resolve faster.
- The price band (2.3), if adopted, means the sample says nothing about the
  model's behaviour on near-certain questions.
- The rule is only as reproducible as the API snapshot. The universe is not
  archived; the scanned records are the record.
