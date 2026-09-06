# Retrieval pipeline

**Status: DRAFT — a design proposal for Jack, not yet in force.** A result in
this study is a result about *model plus this pipeline*; until this file is
signed, nobody can say which half produced it. Signing is an amendment row and
a `prereg-v2` tag. Items marked ▶ are the decisions that need a human.

Three properties, in priority order: **the market price cannot reach the
context**, the pipeline is **mechanical and model-free**, and **everything the
model saw is stored in full**.

---

## 1. Sources

| source | role | notes |
|---|---|---|
| Polymarket market record | question, resolution criteria, close time | already in the prompt; description text is **not** included as evidence (it sometimes quotes the current odds) |
| ▶ web search | evidence | proposed: **Brave Search API** (free tier covers the budget: 420 forecasts × 2 queries). Alternative: a self-hosted SearXNG instance — no key, no vendor, less reliable |
| fetched pages | evidence text | top results from the search, fetched and reduced to text |

No other sources. No model-generated queries, no agentic browsing, no tool use
by the model. The model receives a fixed block of text and nothing else.

## 2. Queries — fixed templates, no model in the loop

Two queries per market, built by string substitution only:

```
q1 = <question with trailing "?" removed>
q2 = <question with trailing "?" removed> + " news"          freshness: past 30 days
```

Both queries request **10** results. Results are merged, de-duplicated by URL,
and ordered: q2 results first (recency), then q1, each in the engine's own
order. Ties and ordering are whatever the engine returned; the harness never
re-ranks.

## 3. The price-leak filter — the part that matters

Retrieved text can carry the very number the anchoring guard exists to hide:
"Polymarket has this at 72%", an odds aggregator, a news article quoting the
market. Three layers, all logged:

**3.1 Domain blocklist (drop the result entirely).** Prediction markets and
their aggregators, always:

```
polymarket.com  kalshi.com  predictit.org  metaculus.com  manifold.markets
polymarketanalytics.com  polymarket.whales.*  electionbettingodds.com
```

▶ **Sportsbooks and odds sites** — proposed: also blocked.

```
oddschecker.com  oddsshark.com  vegasinsider.com  covers.com  actionnetwork.com
draftkings.com  fanduel.com  betmgm.com  caesars.com  bet365.*  pinnacle.com
```

Bookmaker odds are another market's price. Leaving them in makes Q2 a test of
whether the model can paraphrase a sportsbook, which it can. Blocking them
makes the sports slice harder for the model and that is the point. Alternative:
allow them and disclose that sports forecasts are partly odds-copying.

**3.2 Sentence scrub (drop the sentence, keep the document).** Any sentence
matching, case-insensitive:

```
polymarket | kalshi | prediction market | betting odds | bookmaker |
implied probability | odds of | % chance | chance of .{0,20}%  |
\d{1,3}\s?¢ | trading at \d | priced at \d | favou?rite at \d
```

The scrubbed sentence count per document is written to the context header.

**3.3 Assertion.** After scrubbing, if the market's slug or the literal string
"polymarket" still appears anywhere in the evidence block, the document that
contains it is dropped and the event is logged. The evidence block that reaches
the prompt is the one that is stored — the same string, byte for byte.

Known limitation: a sentence like "analysts give it a 70% shot" survives. The
scrub is for market prices, not for every number in the world. The residual
risk is disclosed, not pretended away.

## 4. Fetch and reduce

- Fetch up to the first **8** surviving results. Per-request timeout **10 s**.
  **No retries** (harness invariant 2): use stdlib `urllib` so there is no
  library retry to disable, or if `requests` is used, `Retry(total=0)`
  explicitly. A failed fetch is logged with its error and skipped; it is not
  re-attempted in this cycle or the next.
- HTML → text with the stdlib parser: drop `script`, `style`, `nav`, `header`,
  `footer`, `aside`; collapse whitespace.
- Keep the first **1,500 characters** of each document after reduction.
  Deterministic truncation, no summarisation, no model.
- Stop adding documents at **5** documents or **7,500 characters**, whichever
  first (▶ budget; roughly 2k tokens, chosen for a consumer-GPU context length
  and cycle time).

## 5. The evidence block

```
[1] <title> — <domain> — <published date if the engine gave one, else "n.d.">
<text>

[2] ...
```

`retrieved_chars` in the record is the length of exactly this block.
`retrieval_sources` is the list of URLs in order. Call A and Call B receive the
identical block by construction — one file, read twice.

## 6. Empty retrieval — forecast anyway

If zero documents survive (search down, everything blocked, all fetches
failed), the evidence block is the literal line

```
(no evidence retrieved)
```

`retrieved_chars` is 0 and the forecast **proceeds**. The model forecasting from
priors is a valid data point; skipping the market on empty retrieval would
delete obscure questions from the sample, which is the same selection bias as
retrying. A pipeline-wide collapse is caught by the `retrieval_drift` health
alarm, not by per-market discretion.

## 7. Context file — what is stored

`forecasts/context/<record_id>.txt`, written before Call A, never modified:

```
market_id: ...
retrieval_ts: ...
universe_size: ...          # from the selection step
queries: ["...", "..."]
results_returned: 17   after_domain_block: 11   fetched: 8   kept: 5
per-document: url | http status | chars before/after truncation | sentences scrubbed
--- EVIDENCE BLOCK (verbatim, as prompted) ---
...
```

Full text, not a reference, not a truncation of the truncation. Disk is cheap;
the confound is not recoverable any other way.

## 8. Freshness of the pipeline itself

The search engine, its index, and the web change under the study. That is a
property of every retrieval-augmented forecaster and it is not controlled here;
it is logged. If the engine or the blocklist changes mid-run, that is a config
change and the sample splits (§3.8 of the pre-registration): write the change
to the amendment log, and do not pool.
