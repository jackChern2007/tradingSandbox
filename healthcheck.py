#!/usr/bin/env python3
"""
healthcheck.py - the one-way valve between forecasts/ and health/.

Run by Jack on a schedule, at the cycle interval. Reads forecasts, writes
OPERATIONAL STATISTICS ONLY to health/. No outcome values, no scores ever cross
this boundary -- that is what keeps both the supervisor and Jack blind during
collection. A COUNT of resolved rows is permitted: it carries no direction.

    python3 healthcheck.py --root study
    python3 healthcheck.py --root study --pin <sha256> --interval 600

Pins default to <root>/study_start.json, written once by start_study.py.
Exit 0 = healthy, 1 = at least one HALT alarm, 2 = refused to write (leak).
"""

import argparse, json, os, shutil, statistics, sys
from collections import defaultdict
from datetime import datetime, timezone

START_FILE = "study_start.json"
CLOCK_SKEW_S = 60
SUSTAIN_SAMPLES = 3        # a report on this many consecutive samples -> halt
COLD_START_MULT = 3        # no first cycle within 3x interval -> halt
DEAD_MULT = 3              # no new cycle within 3x interval -> halt

# Key names that must never appear anywhere in health output.
FORBIDDEN_KEYS = ("outcome", "brier", "accuracy", "skill", "logloss", "correct")

# ---- forecast record schema (mirrors schema.md; validated on EVERY record) ----
NoneT = type(None)
NUM = (int, float)
BASE_REQUIRED = {                      # every record, forecast or skipped
    "record_id": (str,), "cycle_id": (str,), "attempt": (int,),
    "model": (str,), "model_sha256": (str,), "prompt_version": (str,),
    "market_id": (str,), "market_slug": (str,), "question": (str,),
    "resolution_criteria": (str,), "category": (str,), "close_time": (str,),
    "scanned": (bool,), "skip_reason": (str, NoneT),
}
FORECAST_REQUIRED = {                  # additionally, when skip_reason is null
    "ts_estimate": (str,), "temperature": NUM, "top_p": NUM, "seed": (int,),
    "context_path": (str,), "retrieved_chars": (int,),
    "retrieval_sources": (list,), "retrieval_ts": (str,),
    "agent_p_verbalized": NUM + (NoneT,), "agent_p_logprob": NUM + (NoneT,),
    "logprob_yes": NUM + (NoneT,), "logprob_no": NUM + (NoneT,),
    "logprob_mass": NUM + (NoneT,), "agent_base_rate": NUM + (NoneT,),
    "evidence_quality": (str, NoneT), "stale": (bool, NoneT),
    "injection_flag": (str, NoneT), "agent_reasoning": (str, NoneT),
    "parse_ok": (bool,), "parse_failure_reason": (str, NoneT),
    "market_p_at_estimate": NUM, "market_bid": NUM, "market_ask": NUM,
    "market_volume_usd": NUM, "price_withheld_from_agent": (bool,),
}
# outcome-shaped (D2) or removed (D3) fields: presence is a schema violation
FORBIDDEN_IN_FORECAST = ("outcome", "resolved", "ts_resolved",
                         "intended_size_frac", "declared_size_cap")


# ---------------- io ----------------

def now():
    return datetime.now(timezone.utc)


def parse_ts(s):
    if not isinstance(s, str):
        return None
    try:
        t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t


def read_jsonl(path):
    """Returns (records, malformed_line_numbers, last_line_partial).
    A malformed LAST line may be a write in progress; anything earlier is a
    corrupted file."""
    recs, bad = [], []
    if not os.path.exists(path):
        return recs, bad, False
    with open(path) as fh:
        lines = fh.read().split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    for i, line in enumerate(lines, 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            recs.append(json.loads(line))
        except json.JSONDecodeError:
            bad.append(i)
    partial = bool(bad) and bad[-1] == len(lines)
    if partial:
        bad = bad[:-1]
    return recs, bad, partial


def count_lines(path):
    if not os.path.exists(path):
        return 0
    with open(path) as fh:
        return sum(1 for line in fh if line.strip() and not line.startswith("#"))


def resolved_market_ids(path):
    """Only market_id is ever taken from a resolution row. The outcome value
    is never bound to a name here."""
    ids = set()
    if not os.path.exists(path):
        return ids
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                ids.add(json.loads(line).get("market_id"))
            except json.JSONDecodeError:
                pass
    ids.discard(None)
    return ids


def load_start(root):
    p = os.path.join(root, START_FILE)
    if not os.path.exists(p):
        return None
    with open(p) as fh:
        return json.load(fh)


def mean(xs, default=0.0):
    xs = [x for x in xs if isinstance(x, NUM) and not isinstance(x, bool)]
    return statistics.fmean(xs) if xs else default


# ---------------- invariants ----------------

def typeok(v, types):
    if isinstance(v, bool) and bool not in types:
        return False
    return isinstance(v, types)


def schema_problems(records):
    out = []
    for i, r in enumerate(records, 1):
        if not isinstance(r, dict):
            out.append(f"record {i}: not an object")
            continue
        rid = r.get("record_id", "?")
        req = dict(BASE_REQUIRED)
        if r.get("skip_reason") is None:
            req.update(FORECAST_REQUIRED)
        for k, t in req.items():
            if k not in r:
                out.append(f"record {i} ({rid}): missing {k}")
            elif not typeok(r[k], t):
                out.append(f"record {i} ({rid}): wrong type for {k}")
        for k in FORBIDDEN_IN_FORECAST:
            if k in r:
                out.append(f"record {i} ({rid}): field '{k}' must not exist "
                           "in a forecast record")
    return out


def clock_problems(records, t_now):
    out, prev = [], None
    for i, r in enumerate(records, 1):
        c = parse_ts(r.get("cycle_id"))
        if c is None:
            out.append(f"record {i}: cycle_id not an ISO8601 timestamp")
            continue
        if prev is not None and c < prev:
            out.append(f"record {i}: cycle_id earlier than a previous cycle "
                       "(non-monotonic)")
        prev = c if prev is None else max(prev, c)
        if (c - t_now).total_seconds() > CLOCK_SKEW_S:
            out.append(f"record {i}: cycle_id is {int((c - t_now).total_seconds())}s "
                       "in the future")
        te = parse_ts(r.get("ts_estimate"))
        if te is not None:
            if (c - te).total_seconds() > CLOCK_SKEW_S:
                out.append(f"record {i}: ts_estimate precedes its cycle_id by "
                           f"{int((c - te).total_seconds())}s")
            if (te - t_now).total_seconds() > CLOCK_SKEW_S:
                out.append(f"record {i}: ts_estimate is in the future")
    return out


def pin_problems(forecasted, start, pin):
    out = []
    if pin:
        hashes = {r.get("model_sha256") for r in forecasted}
        bad = hashes - {pin}
        if bad:
            out.append(f"{len(bad)} model_sha256 value(s) differ from pin")
    if not start:
        return out
    for key in ("prompt_version", "temperature", "top_p", "seed"):
        if key not in start:
            continue
        vals = {r.get(key) for r in forecasted}
        if vals - {start[key]}:
            out.append(f"{key} differs from study_start.json in "
                       f"{sum(1 for r in forecasted if r.get(key) != start[key])} "
                       "record(s)")
    return out


# ---------------- stats ----------------

def compute(records, malformed, partial, start, pin, interval, root, res_path):
    t_now = now()
    cycle_ts = {}
    for r in records:
        c = r.get("cycle_id")
        if isinstance(c, str) and c not in cycle_ts:
            cycle_ts[c] = parse_ts(c)
    cycles = sorted(cycle_ts, key=lambda c: (cycle_ts[c] is None, cycle_ts[c] or t_now, c))
    last = cycles[-1] if cycles else None
    lastrecs = [r for r in records if r.get("cycle_id") == last]
    forecasted = [r for r in records if r.get("skip_reason") is None]
    n = len(forecasted) or 1

    parsed = [r for r in forecasted if r.get("parse_ok") is not False]
    fails = defaultdict(int)
    for r in forecasted:
        if r.get("parse_ok") is False:
            fails[r.get("parse_failure_reason") or "unspecified"] += 1

    pv = [r.get("agent_p_verbalized") for r in parsed]
    pv = [p for p in pv if isinstance(p, NUM) and not isinstance(p, bool)]
    gaps = [a - b for a, b in
            zip((r.get("agent_p_verbalized") for r in parsed),
                (r.get("agent_p_logprob") for r in parsed))
            if isinstance(a, NUM) and isinstance(b, NUM)]

    # liveness clocks
    secs_last = None
    if last and cycle_ts.get(last) is not None:
        secs_last = (t_now - cycle_ts[last]).total_seconds()
    start_ts = (start or {}).get("study_start_ts")
    secs_start = None
    if parse_ts(start_ts) is not None:
        secs_start = (t_now - parse_ts(start_ts)).total_seconds()

    # forecasts per completed cycle (the last cycle may be in progress)
    per_cycle = defaultdict(int)
    for r in forecasted:
        per_cycle[r.get("cycle_id")] += 1
    completed = cycles[:-1]
    counts = [per_cycle[c] for c in completed]
    last_complete = counts[-1] if counts else None
    prev_median = (statistics.median(counts[-21:-1])
                   if len(counts) >= 6 else None)

    # resolved counts -- integers only, never a direction
    res_ids = resolved_market_ids(res_path)
    resolved_forecasts = sum(1 for r in parsed if r.get("market_id") in res_ids)

    schema = schema_problems(records)
    clock = clock_problems(records, t_now)
    pins = pin_problems(forecasted, start, pin)

    return {
        "ts": t_now.isoformat(),
        "study_start_ts": start_ts,
        "seconds_since_study_start": (round(secs_start, 1)
                                      if secs_start is not None else None),
        "cycles_total": len(cycles),
        "records_total": len(records),
        "forecasts_total": len(forecasted),
        "records_last_cycle": len(lastrecs),
        "forecasts_last_complete_cycle": last_complete,
        "forecasts_per_cycle_median": prev_median,
        "parse_ok_rate": round(len(parsed) / n, 4),
        "parse_failures_by_reason": dict(fails),
        "anchor_guard_rate": round(
            sum(1 for r in forecasted
                if r.get("price_withheld_from_agent") is True) / n, 4),
        "attempt_violations": sum(1 for r in forecasted
                                  if r.get("attempt", 1) != 1),
        "mean_retrieved_chars": round(mean(r.get("retrieved_chars")
                                           for r in forecasted), 1),
        "thin_evidence_rate": round(
            sum(1 for r in parsed if r.get("evidence_quality") == "thin")
            / max(len(parsed), 1), 4),
        "mean_abs_p_minus_half": round(mean(abs(p - 0.5) for p in pv), 4),
        "p_exactly_half_rate": round(
            sum(1 for p in pv if abs(p - 0.5) < 1e-9) / max(len(pv), 1), 4),
        "mean_verbalized_minus_logprob": round(mean(gaps), 4),
        "injection_flags_total": sum(1 for r in forecasted
                                     if r.get("injection_flag")),
        "resolved_count": count_lines(res_path),
        "resolved_forecasts": resolved_forecasts,
        "model_sha256_ok": not any("model_sha256" in p for p in pins),
        "pin_violations": len(pins),
        "schema_violations": len(schema),
        "clock_violations": len(clock),
        "malformed_lines": len(malformed),
        "partial_last_line": partial,
        "disk_free_gb": round(shutil.disk_usage(root).free / 2**30, 2),
        "seconds_since_last_cycle": (round(secs_last, 1)
                                     if secs_last is not None else None),
        "active_reports": [],
        "_expected_interval_s": interval,
        "_schema_problems": schema[:5],
        "_clock_problems": clock[:5],
        "_pin_problems": pins[:5],
        "_malformed_lines": malformed[:5],
    }


# ---------------- alarms ----------------

def alarms(h, history, start_present):
    """Pre-committed thresholds (SUPERVISOR.md). halt = stop the run.
    Mutates h["active_reports"] so escalation is derivable from history."""
    out = []

    def add(sev, check, detail):
        out.append({"ts": h["ts"], "severity": sev, "check": check,
                    "detail": detail})

    def few(xs):
        return "; ".join(xs[:3]) + (" ..." if len(xs) > 3 else "")

    iv = h["_expected_interval_s"]

    # -- mechanical invariants: halt --
    if not start_present:
        add("halt", "study_start_missing",
            f"{START_FILE} not found - run start_study.py; liveness cannot be "
            "judged without a start timestamp")
    if h["records_total"] == 0:
        if (h["seconds_since_study_start"] is not None
                and h["seconds_since_study_start"] > COLD_START_MULT * iv):
            add("halt", "cold_start",
                f"no forecast written {h['seconds_since_study_start']:.0f}s after "
                f"study start (> {COLD_START_MULT}x interval) - harness never started")
    else:
        s = h["seconds_since_last_cycle"]
        if s is not None and s > DEAD_MULT * iv:
            add("halt", "harness_alive", f"no cycle in {s:.0f}s")
        if s is not None and s < -CLOCK_SKEW_S:
            add("halt", "clock", f"last cycle_id is {-s:.0f}s in the future")
        elif s is not None and s < 0:
            add("report", "clock", f"last cycle_id is {-s:.0f}s ahead of this host")
    if h["clock_violations"]:
        add("halt", "clock",
            f"{h['clock_violations']} timestamp violation(s): "
            f"{few(h['_clock_problems'])}")
    if h["schema_violations"]:
        add("halt", "schema",
            f"{h['schema_violations']} record(s) fail schema: "
            f"{few(h['_schema_problems'])}")
    if h["malformed_lines"]:
        add("halt", "schema",
            f"{h['malformed_lines']} unparseable line(s) before end of file: "
            f"{few([str(x) for x in h['_malformed_lines']])}")
    if h["partial_last_line"]:
        add("report", "partial_write",
            "last line of forecasts.jsonl is not valid JSON (write in progress?)")
    if h["pin_violations"]:
        sev = "halt"
        add(sev, "model_integrity" if not h["model_sha256_ok"] else "config_pin",
            few(h["_pin_problems"]))
    if h["forecasts_total"] and h["anchor_guard_rate"] < 1.0:
        add("halt", "anchoring_guard",
            f"price_withheld rate {h['anchor_guard_rate']:.3f} < 1.0")
    if h["attempt_violations"] > 0:
        add("halt", "retry_discipline",
            f"{h['attempt_violations']} records with attempt != 1")
    if h["disk_free_gb"] < 2.0:
        add("halt", "disk", f"{h['disk_free_gb']} GB free")
    if h["forecasts_total"] and h["parse_ok_rate"] < 0.75:
        add("halt", "parse_failures",
            f"parse_ok {h['parse_ok_rate']:.2f} < 0.75 (stop cond. 6.6)")

    # -- drift signals: report; sustained -> halt --
    tail = [x for x in history[-20:] if x.get("mean_retrieved_chars")]
    if len(tail) >= 5:
        med = statistics.median(x["mean_retrieved_chars"] for x in tail)
        if med > 0 and h["mean_retrieved_chars"] < 0.5 * med:
            add("report", "retrieval_drift",
                f"retrieved_chars {h['mean_retrieved_chars']:.0f} < 50% of "
                f"trailing median {med:.0f} - retrieval may have broken silently")
    if len(history) >= 5:
        base = statistics.median(x.get("thin_evidence_rate", 0) for x in history[-20:])
        if h["thin_evidence_rate"] - base > 0.30:
            add("report", "evidence_quality_drift",
                f"thin-evidence share up {h['thin_evidence_rate']-base:+.2f}")
    if h["forecasts_total"] and h["mean_abs_p_minus_half"] < 0.05:
        add("report", "no_discrimination",
            "mean |p-0.5| < 0.05 - model has stopped discriminating")
    if h["p_exactly_half_rate"] > 0.20:
        add("report", "refusal_to_commit",
            f"{h['p_exactly_half_rate']:.0%} of forecasts are exactly 0.50")
    if abs(h["mean_verbalized_minus_logprob"]) > 0.10:
        add("report", "elicitation_divergence",
            f"verb-logprob gap {h['mean_verbalized_minus_logprob']:+.3f} "
            "- check tokenization")
    lc, med = h["forecasts_last_complete_cycle"], h["forecasts_per_cycle_median"]
    if lc is not None and med and abs(lc - med) > 0.5 * med:
        add("report", "selection_drift",
            f"{lc} forecasts in last complete cycle vs trailing median {med:.0f} "
            "- selection rule drifting?")

    # -- escalation: same report on SUSTAIN_SAMPLES consecutive samples --
    active = sorted({a["check"] for a in out if a["severity"] == "report"})
    h["active_reports"] = active
    prev = [set(x.get("active_reports") or []) for x in history[-(SUSTAIN_SAMPLES - 1):]]
    if len(prev) == SUSTAIN_SAMPLES - 1 and prev:
        for a in out:
            if a["severity"] == "report" and all(a["check"] in p for p in prev):
                a["severity"] = "halt"
                a["detail"] = (f"sustained {SUSTAIN_SAMPLES} consecutive health "
                               f"samples: {a['detail']}")
    return out


def leaked_keys(obj, path=""):
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if any(f in str(k).lower() for f in FORBIDDEN_KEYS):
                out.append(path + str(k))
            out += leaked_keys(v, path + str(k) + ".")
    elif isinstance(obj, list):
        for v in obj:
            out += leaked_keys(v, path)
    return out


# ---------------- main ----------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="study")
    ap.add_argument("--pin", default=None,
                    help="expected model_sha256 (default: study_start.json)")
    ap.add_argument("--interval", type=int, default=None,
                    help="expected seconds between cycles (default: study_start.json)")
    a = ap.parse_args()

    start = load_start(a.root)
    pin = a.pin or (start or {}).get("model_sha256")
    interval = a.interval or (start or {}).get("expected_interval_s") or 600
    config_conflicts = []
    if start and a.pin and start.get("model_sha256") != a.pin:
        config_conflicts.append("--pin differs from study_start.json")
    if start and a.interval and start.get("expected_interval_s") != a.interval:
        config_conflicts.append("--interval differs from study_start.json")

    fdir = os.path.join(a.root, "forecasts")
    fpath = os.path.join(fdir, "forecasts.jsonl")
    rpath = os.path.join(fdir, "resolutions.jsonl")
    hdir = os.path.join(a.root, "health")
    os.makedirs(hdir, exist_ok=True)
    hpath, apath = os.path.join(hdir, "health.jsonl"), os.path.join(hdir, "alarms.jsonl")

    history, _, _ = read_jsonl(hpath)
    records, malformed, partial = read_jsonl(fpath)
    h = compute(records, malformed, partial, start, pin, interval, a.root, rpath)
    fired = alarms(h, history, start is not None)
    for c in config_conflicts:
        fired.insert(0, {"ts": h["ts"], "severity": "halt",
                         "check": "config_mismatch", "detail": c})

    leaked = leaked_keys(h) + leaked_keys(fired)
    if leaked:
        print(f"REFUSING TO WRITE: blinding leak in {leaked}", file=sys.stderr)
        return 2

    with open(hpath, "a") as fh:
        fh.write(json.dumps(h) + "\n")
        fh.flush(); os.fsync(fh.fileno())
    if fired:
        with open(apath, "a") as fh:
            for al in fired:
                fh.write(json.dumps(al) + "\n")
            fh.flush(); os.fsync(fh.fileno())

    print(json.dumps(h, indent=2))
    for al in fired:
        print(f"[{al['severity'].upper()}] {al['check']}: {al['detail']}",
              file=sys.stderr)
    return 1 if any(x["severity"] == "halt" for x in fired) else 0


if __name__ == "__main__":
    sys.exit(main())
