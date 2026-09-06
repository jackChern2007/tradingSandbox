#!/usr/bin/env python3
"""
score.py - Scoring for the local-model vs market calibration study.
Run ONCE, after the pre-committed N is reached. Not during collection.

Primary metric (pre-committed, PREREGISTRATION.md sec.7): paired difference in
Brier score, market minus model, using the VERBALIZED probability, with a 95%
paired bootstrap CI. Positive means the model is better.

Everything else printed here is diagnostic. A favourable diagnostic beside a
null primary is a null result.

Forecasts are immutable once written; outcomes live in a separate append-only
resolutions file keyed by market_id and are joined here. A forecast with no
matching resolution is simply unresolved.

Stdlib only.
    python3 score.py forecasts/forecasts.jsonl
    python3 score.py forecasts/forecasts.jsonl --min-n 300 --boot 20000
    python3 score.py syn_null.jsonl --resolutions syn_null.resolutions.jsonl
"""

import argparse, json, math, os, random, sys
from collections import defaultdict

EPS = 1e-6
PRIMARY = "agent_p_verbalized"
SECONDARY = "agent_p_logprob"
# outcome-shaped keys that must never appear in a forecast record (D2)
OUTCOME_KEYS = ("outcome", "resolved", "ts_resolved")


# ---------------- loading & gating ----------------

def load(path):
    recs, bad = [], 0
    with open(path) as fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                bad += 1
                print(f"  ! malformed line {i}", file=sys.stderr)
    return recs, bad


def default_resolutions_path(logfile):
    """<stem>.resolutions.jsonl beside the log if it exists (fixtures), else
    resolutions.jsonl in the same directory (the study layout)."""
    stem = logfile[:-6] if logfile.endswith(".jsonl") else logfile
    cand = stem + ".resolutions.jsonl"
    if os.path.exists(cand):
        return cand
    return os.path.join(os.path.dirname(logfile) or ".", "resolutions.jsonl")


def load_resolutions(path):
    """market_id -> outcome. A market resolved twice with the same outcome is
    fine; conflicting outcomes make the market unscoreable."""
    res, conflicts, bad = {}, set(), 0
    if not os.path.exists(path):
        return res, conflicts, bad
    recs, bad = load(path)
    for r in recs:
        mid, y = r.get("market_id"), r.get("outcome")
        if mid is None:
            bad += 1
            continue
        if mid in res and res[mid] != y:
            conflicts.add(mid)
        res.setdefault(mid, y)
    return res, conflicts, bad


def audit(records, resolutions, conflicts):
    """Split into scoreable rows, parse-failures, and rejects. Gates are
    pre-committed and are not negotiable after the fact."""
    ok, parse_fail, rejects = [], [], defaultdict(int)
    seen = set()
    attempts = 0          # every forecast attempt, resolved or not
    for r in records:
        if r.get("scanned") and r.get("skip_reason"):
            rejects["skipped by selection rule"] += 1
            continue
        if r.get("attempt", 1) != 1:
            rejects["RETRY DETECTED - protocol violation"] += 1
            continue
        if r.get("price_withheld_from_agent") is not True:
            rejects["anchoring guard failed"] += 1
            continue
        if any(k in r for k in OUTCOME_KEYS):
            rejects["outcome field inside forecast record - schema violation"] += 1
            continue
        attempts += 1
        if r.get("parse_ok") is False:
            parse_fail.append(r)
            continue
        key = (r.get("market_id"), r.get("cycle_id"))
        if key in seen:
            rejects["duplicate market+cycle"] += 1
            continue
        seen.add(key)
        mid = r.get("market_id")
        if mid in conflicts:
            rejects["conflicting resolutions for market"] += 1
            continue
        if mid not in resolutions:
            rejects["not yet resolved"] += 1
            continue
        y = resolutions[mid]
        if y not in (0, 1):
            rejects["outcome not 0/1"] += 1
            continue
        pv, pm = r.get(PRIMARY), r.get("market_p_at_estimate")
        if pv is None or pm is None:
            rejects["missing probability"] += 1
            continue
        if not (0.0 < pv < 1.0) or not (0.0 <= pm <= 1.0):
            rejects["probability out of range"] += 1
            continue
        row = dict(r)
        row["outcome"] = y
        ok.append(row)
    return ok, parse_fail, rejects, attempts


# ---------------- scoring rules ----------------

def brier(ps, ys):
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ps)


def logloss(ps, ys):
    t = 0.0
    for p, y in zip(ps, ys):
        q = min(max(p, EPS), 1 - EPS)
        t += -(y * math.log(q) + (1 - y) * math.log(1 - q))
    return t / len(ps)


def paired_bootstrap(pa, pm, ys, n_boot, seed=20260828):
    rng = random.Random(seed)
    n = len(ys)
    diffs = [((m - y) ** 2) - ((a - y) ** 2) for a, m, y in zip(pa, pm, ys)]
    point = sum(diffs) / n
    boots = []
    for _ in range(n_boot):
        s = 0.0
        for _ in range(n):
            s += diffs[rng.randrange(n)]
        boots.append(s / n)
    boots.sort()
    lo = boots[int(0.025 * n_boot)]
    hi = boots[min(int(0.975 * n_boot), n_boot - 1)]
    return point, lo, hi, sum(1 for b in boots if b > 0) / n_boot


def calibration(ps, ys, width=0.1):
    bins = defaultdict(lambda: [0, 0.0, 0])
    for p, y in zip(ps, ys):
        i = min(int(p / width), int(1 / width) - 1)
        b = bins[i]; b[0] += 1; b[1] += p; b[2] += y
    return [(i * width, (i + 1) * width, n, sp / n, sy / n)
            for i, (n, sp, sy) in sorted(bins.items())]


def sharpness(ps):
    return sum(abs(p - 0.5) for p in ps) / len(ps)


def bar(f, w=20):
    return "#" * int(round(f * w)) + "." * (w - int(round(f * w)))


# ---------------- report ----------------

def report(rows, parse_fail, attempts, min_n, n_boot):
    pv = [r[PRIMARY] for r in rows]
    pm = [r["market_p_at_estimate"] for r in rows]
    ys = [r["outcome"] for r in rows]
    n = len(ys)

    bv, bm = brier(pv, ys), brier(pm, ys)
    point, lo, hi, pgt = paired_bootstrap(pv, pm, ys, n_boot)
    bss = 1 - bv / bm if bm > 0 else float("nan")

    print("=" * 68)
    print("PRIMARY RESULT  (verbalized probability vs market price)".center(68))
    print("=" * 68)
    print(f"  Resolved forecasts scored : {n}")
    print(f"  Base rate (YES)           : {sum(ys)/n:.3f}")
    print()
    print(f"  Brier - model (verbalized): {bv:.4f}   (lower is better)")
    print(f"  Brier - market            : {bm:.4f}")
    print(f"  Brier skill score vs mkt  : {bss:+.4f}")
    print()
    print("  Paired difference, market minus model:")
    print(f"    point estimate          : {point:+.5f}")
    print(f"    95% bootstrap CI        : [{lo:+.5f}, {hi:+.5f}]")
    print(f"    bootstrap P(diff > 0)   : {pgt:.3f}")
    print()
    if n < min_n:
        print(f"  >> UNDERPOWERED. Committed minimum is {min_n}; you have {n}.")
        print("     Do not interpret the numbers above. Keep collecting.")
    elif lo > 0:
        print("  >> Model beats the market price; CI excludes zero.")
        print("     Per sec.6.3 this requires replication on a fresh 300")
        print("     before any funded phase is even discussed.")
    elif hi < 0:
        print("  >> Market beats the model; CI excludes zero.")
        print("     Stop condition 6.1 met. Do not fund.")
    else:
        print("  >> No detectable difference; CI spans zero.")
        print("     Stop condition 6.2 met. This is the expected outcome and")
        print("     it is a complete answer. Do not fund.")
    print()

    # ---- parse failures ----
    n_fail = len(parse_fail)
    tot = attempts                      # ALL forecast attempts, not just resolved
    print("-" * 68)
    print("PARSE FAILURES  (a result, not an inconvenience)")
    print("-" * 68)
    rate = n_fail / tot if tot else 0.0
    print(f"  Failures: {n_fail} of {tot} forecast attempts  ({rate:.1%})")
    print(f"  (denominator is every attempt, resolved or not; {n} of the")
    print(f"   successful parses have resolved and are scored above)")
    if n_fail:
        by = defaultdict(int)
        for r in parse_fail:
            by[r.get("parse_failure_reason") or "unspecified"] += 1
        for k, v in sorted(by.items(), key=lambda x: -x[1]):
            print(f"    {v:5d}  {k}")
    if rate > 0.25:
        print("  >> Exceeds the 25% stop threshold (sec.6.6): prompt/model")
        print("     mismatch. This sample should not be used.")
    print()

    # ---- verbalized vs logprob (Q3) ----
    both = [r for r in rows if isinstance(r.get(SECONDARY), (int, float))]
    print("-" * 68)
    print("Q3 - VERBALIZED vs LOGPROB")
    print("-" * 68)
    if len(both) < 20:
        print(f"  Only {len(both)} rows carry both probabilities. Skipping.")
    else:
        a = [r[PRIMARY] for r in both]
        b = [r[SECONDARY] for r in both]
        yb = [r["outcome"] for r in both]
        gaps = [x - y for x, y in zip(a, b)]
        mad = sum(abs(g) for g in gaps) / len(gaps)
        print(f"  n paired            : {len(both)}")
        print(f"  mean(verb - logprob): {sum(gaps)/len(gaps):+.4f}")
        print(f"  mean |difference|   : {mad:.4f}")
        print(f"  Brier - verbalized  : {brier(a, yb):.4f}")
        print(f"  Brier - logprob     : {brier(b, yb):.4f}")
        rounds = sum(1 for p in a if abs(p * 20 - round(p * 20)) < 1e-9)
        print(f"  verbalized on 0.05 grid: {rounds/len(a):.1%}  "
              "(clustering on round numbers)")
    print()

    # ---- diagnostics ----
    print("-" * 68)
    print("DIAGNOSTICS (secondary - never substitute for the primary)")
    print("-" * 68)
    print(f"  Log loss  - model  : {logloss(pv, ys):.4f}")
    print(f"  Log loss  - market : {logloss(pm, ys):.4f}")
    print(f"  Sharpness - model  : {sharpness(pv):.4f}   (mean |p - 0.5|)")
    print(f"  Sharpness - market : {sharpness(pm):.4f}")
    print(f"  Mean p    - model  : {sum(pv)/n:.4f}")
    print(f"  Observed rate      : {sum(ys)/n:.4f}")
    inj = sum(1 for r in rows if r.get("injection_flag"))
    print(f"  Injection flags    : {inj}")
    print()
    print("  Model calibration by decile:")
    print("    bin          n   mean_p   observed   reliability")
    for a_, b_, c, mp, ob in calibration(pv, ys):
        print(f"    {a_:.1f}-{b_:.1f} {c:6d}   {mp:.3f}    {ob:.3f}   "
              f"{ob-mp:+.3f} {bar(ob)}")
    print()
    print("  Calibrated means observed ~= mean_p in every row.")
    print("  Negative gaps at high p and positive at low p = overconfidence (H1).")
    print("=" * 68)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("logfile")
    ap.add_argument("--resolutions", default=None,
                    help="resolutions.jsonl (default: beside the logfile)")
    ap.add_argument("--min-n", type=int, default=300)
    ap.add_argument("--boot", type=int, default=20000)
    a = ap.parse_args()

    rpath = a.resolutions or default_resolutions_path(a.logfile)
    recs, bad = load(a.logfile)
    resolutions, conflicts, rbad = load_resolutions(rpath)
    rows, pfail, rejects, attempts = audit(recs, resolutions, conflicts)

    print(f"\nLoaded {len(recs)} records ({bad} malformed lines).")
    print(f"Resolutions: {len(resolutions)} markets from {rpath} "
          f"({rbad} malformed, {len(conflicts)} conflicting).")
    print(f"Scoreable: {len(rows)}   parse-failures: {len(pfail)}")
    if rejects:
        print("Excluded:")
        for k, v in sorted(rejects.items()):
            print(f"   {v:5d}  {k}")
        if any("RETRY" in k for k in rejects):
            print("\n" + "!" * 68)
            print("  RETRIES PRESENT. Protocol sec.3.3 violated.")
            print("  The sample is biased toward easy questions in a way that")
            print("  cannot be corrected after the fact.")
            print("  REFUSING TO SCORE. Fix the harness and restart the sample.")
            print("!" * 68 + "\n")
            return 3
    print()
    if not rows:
        print("Nothing to score yet.")
        return 0
    report(rows, pfail, attempts, a.min_n, a.boot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
