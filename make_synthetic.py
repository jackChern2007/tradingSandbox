#!/usr/bin/env python3
"""
make_synthetic.py - fixtures with known ground truth, in the real schema.

Validate the instrument BEFORE it touches real data: confirm the scorer flags a
known-overconfident forecaster, flags a known-worse one, refuses a retry, and
stays silent on a known-null one.

Each fixture is two files (D2): <name>.jsonl holds immutable forecast records,
<name>.resolutions.jsonl holds outcomes keyed by market_id. score.py finds the
second file on its own.

    python3 make_synthetic.py
    python3 score.py syn_null.jsonl     --min-n 300 --boot 4000   # CI spans zero
    python3 score.py syn_worse.jsonl    --min-n 300 --boot 4000   # market wins
    python3 score.py syn_overconf.jsonl --min-n 300 --boot 4000   # market wins
    python3 score.py syn_retry.jsonl    --min-n 300 --boot 4000   # exit 3
"""
import json, random, uuid
from datetime import datetime, timedelta, timezone

PIN = "a" * 64
PROMPT_VERSION = "v3"
DEFAULT_START = datetime(2026, 8, 1, tzinfo=timezone.utc)


def gen(path, scenario, n=320, seed=7, parse_fail_rate=0.06, inject_retry=False,
        start=DEFAULT_START, interval_s=600, per_cycle=2, pin=PIN,
        prompt_version=PROMPT_VERSION):
    """Write <path> and <stem>.resolutions.jsonl. `start`, `interval_s` and
    `per_cycle` shape the cycle timeline so healthcheck.py can be exercised
    against a fixture with a realistic clock."""
    rng = random.Random(seed)
    rpath = path[:-6] + ".resolutions.jsonl"
    with open(path, "w") as fh, open(rpath, "w") as rh:
        for i in range(n):
            cycle = start + timedelta(seconds=(i // per_cycle) * interval_s)
            tp = rng.betavariate(2, 2)
            mkt = min(max(tp + rng.gauss(0, .06), .01), .99)
            if scenario == "null":
                # TRUE null: independent noise of the SAME size the market has,
                # around the SAME truth. Not market+noise -- that is strictly
                # worse by construction and can never read as null.
                pv = min(max(tp + rng.gauss(0, .06), .01), .99)
            elif scenario == "worse":
                # deliberately inferior: market price plus extra noise
                pv = min(max(mkt + rng.gauss(0, .06), .01), .99)
            elif scenario == "edge":
                pv = min(max(tp + rng.gauss(0, .02), .01), .99)
            elif scenario == "overconf":
                a = min(max(tp + rng.gauss(0, .06), .01), .99)
                pv = min(max(.5 + (a - .5) * 2.2, .01), .99)
            else:
                raise ValueError(scenario)
            # verbalized clusters on the 0.05 grid; logprob does not
            pv_round = min(max(round(pv * 20) / 20, .01), .99)
            pl = min(max(pv + rng.gauss(0, .04), .01), .99)
            y = 1 if rng.random() < tp else 0
            failed = rng.random() < parse_fail_rate
            base_rate = round(rng.uniform(.2, .8), 3)
            evidence = rng.choice(["strong", "mixed", "thin"])
            # Stream-compat: the retired sizing probe (D3) drew here. The draws
            # are kept so every fixture stays byte-identical to the validated
            # set; the values go nowhere.
            if rng.random() >= .08:
                rng.uniform(.2, 1.0)
            rec = {
                "record_id": str(uuid.uuid4()),
                "cycle_id": cycle.isoformat(),
                "ts_estimate": (cycle + timedelta(seconds=30)).isoformat(),
                "attempt": 2 if (inject_retry and i == 5) else 1,
                "model": "qwen3.5-local", "model_sha256": pin,
                "temperature": 0.0, "top_p": 1.0, "seed": 1234,
                "prompt_version": prompt_version,
                "market_id": f"m{i}", "market_slug": f"synthetic-{i}",
                "question": f"Synthetic question {i}?",
                "resolution_criteria": "synthetic", "category": "test",
                "close_time": "2026-10-01T00:00:00+00:00",
                "scanned": True, "skip_reason": None,
                "context_path": f"context/{i}.txt",
                "retrieved_chars": int(rng.gauss(4000, 600)),
                "retrieval_sources": ["synthetic"],
                "retrieval_ts": (cycle + timedelta(seconds=10)).isoformat(),
                "agent_p_verbalized": None if failed else round(pv_round, 4),
                "agent_p_logprob": None if failed else round(pl, 4),
                "logprob_yes": None if failed else -0.5,
                "logprob_no": None if failed else -1.2,
                "logprob_mass": None if failed else 0.82,
                "agent_base_rate": None if failed else base_rate,
                "evidence_quality": None if failed else evidence,
                "stale": None if failed else False,
                "injection_flag": None,
                "agent_reasoning": None if failed else "synthetic",
                "parse_ok": not failed,
                "parse_failure_reason": "malformed_json" if failed else None,
                "market_p_at_estimate": round(mkt, 4),
                "market_bid": round(max(mkt - .01, .01), 4),
                "market_ask": round(min(mkt + .01, .99), 4),
                "market_volume_usd": 50000.0,
                "price_withheld_from_agent": True,
            }
            fh.write(json.dumps(rec) + "\n")
            rh.write(json.dumps({
                "market_id": f"m{i}", "outcome": y,
                "ts_resolved": "2026-10-02T00:00:00+00:00",
                "source": "synthetic",
            }) + "\n")


if __name__ == "__main__":
    gen("syn_null.jsonl", "null")
    gen("syn_worse.jsonl", "worse")
    gen("syn_edge.jsonl", "edge")
    gen("syn_overconf.jsonl", "overconf")
    gen("syn_retry.jsonl", "null", inject_retry=True)
    print("generated: syn_null, syn_worse, syn_edge, syn_overconf, syn_retry "
          "(+ .resolutions.jsonl each)")
