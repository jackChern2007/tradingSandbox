#!/usr/bin/env python3
"""
devlog.py - factual digest for a devlog entry, built ONLY from health/.

The supervisor writes prose around the numbers this emits. It never invents a
number and never reads forecasts/, so a devlog entry cannot leak outcomes even
by accident. A resolved COUNT is permitted (progress toward N carries no
direction); outcome values, Brier, accuracy and anything score-shaped are not.

    python3 devlog.py --root study --since 2026-09-01T00:00:00+00:00
    python3 devlog.py --root study --since 7d --json
"""

import argparse, json, os, re, sys
from datetime import datetime, timedelta, timezone

TARGET_N = 300
# forbidden as (sub)strings of any key in the digest
FORBIDDEN_KEYS = ("outcome", "brier", "accuracy", "skill", "logloss", "correct")
# forbidden inside any string value (alarm details, notes)
FORBIDDEN_VALUES = ("brier", "accuracy", "skill score", "log loss", "win rate")


def load(path):
    out = []
    if os.path.exists(path):
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    return out


def parse_since(s):
    if not s:
        return None
    m = re.fullmatch(r"(\d+)([dh])", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        delta = timedelta(days=n) if unit == "d" else timedelta(hours=n)
        return datetime.now(timezone.utc) - delta
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def ts(rec):
    return datetime.fromisoformat(rec["ts"].replace("Z", "+00:00"))


def digest(health, alarms, since):
    window = [h for h in health if since is None or ts(h) >= since]
    if len(window) < 2:
        return None
    a, b = window[0], window[-1]
    aw = [x for x in alarms if since is None or ts(x) >= since]

    def d(k):
        return (b.get(k) or 0) - (a.get(k) or 0)

    def trend(k, pct=False):
        x, y = a.get(k), b.get(k)
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            return None
        return {"start": round(x, 4), "end": round(y, 4),
                "delta": round(y - x, 4),
                "pct_change": (round((y - x) / x * 100, 1)
                               if pct and x else None)}

    fails = {}
    for k, v in (b.get("parse_failures_by_reason") or {}).items():
        fails[k] = v - (a.get("parse_failures_by_reason") or {}).get(k, 0)
    fails = {k: v for k, v in fails.items() if v > 0}

    halts = [x for x in aw if x.get("severity") == "halt"]
    reports = [x for x in aw if x.get("severity") == "report"]
    resolved = b.get("resolved_forecasts")

    return {
        "period_start": a["ts"],
        "period_end": b["ts"],
        "health_samples": len(window),
        "study_start_ts": b.get("study_start_ts"),
        "cycles_added": d("cycles_total"),
        "records_added": d("records_total"),
        "records_total_end": b.get("records_total"),
        "resolved_markets_end": b.get("resolved_count"),
        "resolved_forecasts_end": resolved,
        "resolved_forecasts_added": d("resolved_forecasts"),
        "target_n": TARGET_N,
        "progress_pct": (round(100 * resolved / TARGET_N, 1)
                         if isinstance(resolved, int) else None),
        "parse_ok_rate": trend("parse_ok_rate"),
        "new_parse_failures_by_reason": fails,
        "mean_retrieved_chars": trend("mean_retrieved_chars", pct=True),
        "thin_evidence_rate": trend("thin_evidence_rate"),
        "mean_abs_p_minus_half": trend("mean_abs_p_minus_half"),
        "p_exactly_half_rate": trend("p_exactly_half_rate"),
        "mean_verbalized_minus_logprob": trend("mean_verbalized_minus_logprob"),
        "injection_flags_added": d("injection_flags_total"),
        "anchor_guard_rate_end": b.get("anchor_guard_rate"),
        "attempt_violations_end": b.get("attempt_violations"),
        "model_sha256_ok_end": b.get("model_sha256_ok"),
        "schema_violations_end": b.get("schema_violations"),
        "active_reports_end": b.get("active_reports") or [],
        "halts": halts,
        "reports": reports,
        "blinding_note": ("outcome values withheld by design - the study is "
                          "blind until N is reached; only counts appear here"),
    }


def render(g):
    L = []
    w = L.append
    w("# Devlog digest\n")
    w(f"**Window:** {g['period_start']} to {g['period_end']}  "
      f"({g['health_samples']} health samples)\n")
    w("## Throughput and progress")
    w(f"- cycles: +{g['cycles_added']}")
    w(f"- records: +{g['records_added']}  (total {g['records_total_end']})")
    w(f"- resolved forecasts: {g['resolved_forecasts_end']} of {g['target_n']} "
      f"({g['progress_pct']}%)  (+{g['resolved_forecasts_added']} this window; "
      f"{g['resolved_markets_end']} markets resolved)\n")

    w("## Instrument integrity")
    w(f"- anchoring guard: {g['anchor_guard_rate_end']}  (must be 1.0)")
    w(f"- retry violations: {g['attempt_violations_end']}  (must be 0)")
    w(f"- model hash matches pin: {g['model_sha256_ok_end']}")
    w(f"- schema violations: {g['schema_violations_end']}  (must be 0)\n")

    p = g["parse_ok_rate"]
    w("## Parse behaviour")
    if p:
        w(f"- parse_ok rate: {p['start']:.3f} -> {p['end']:.3f} "
          f"({p['delta']:+.3f})")
    if g["new_parse_failures_by_reason"]:
        w("- new failures this window:")
        for k, v in sorted(g["new_parse_failures_by_reason"].items(),
                           key=lambda x: -x[1]):
            w(f"    {v:5d}  {k}")
    else:
        w("- no new parse failures")
    w("")

    w("## Signals that need no outcomes")
    for label, key, fmt in [
        ("retrieved chars (mean)", "mean_retrieved_chars", "{:.0f}"),
        ("thin-evidence share", "thin_evidence_rate", "{:.3f}"),
        ("sharpness |p-0.5|", "mean_abs_p_minus_half", "{:.3f}"),
        ("p exactly 0.50 share", "p_exactly_half_rate", "{:.3f}"),
        ("verbalized minus logprob", "mean_verbalized_minus_logprob", "{:+.4f}"),
    ]:
        t = g[key]
        if t:
            s, e = fmt.format(t["start"]), fmt.format(t["end"])
            pc = f"  ({t['pct_change']:+.1f}%)" if t.get("pct_change") else ""
            w(f"- {label}: {s} -> {e}{pc}")
    w(f"- injection flags raised: +{g['injection_flags_added']}\n")

    w("## Alarms")
    if not g["halts"] and not g["reports"]:
        w("- none")
    for x in g["halts"]:
        w(f"- **HALT** `{x['check']}` - {x['detail']}")
    for x in g["reports"]:
        w(f"- report `{x['check']}` - {x['detail']}")
    if g["active_reports_end"]:
        w(f"- still active at window end: {', '.join(g['active_reports_end'])}")
    w("")
    w(f"> {g['blinding_note']}")
    return "\n".join(L)


def leaks(obj, path=""):
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if any(f in str(k).lower() for f in FORBIDDEN_KEYS):
                out.append(f"key {path}{k}")
            out += leaks(v, f"{path}{k}.")
    elif isinstance(obj, list):
        for v in obj:
            out += leaks(v, path)
    elif isinstance(obj, str):
        low = obj.lower()
        for f in FORBIDDEN_VALUES:
            if f in low:
                out.append(f"value at {path or '<root>'} contains '{f}'")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="study")
    ap.add_argument("--since", default=None,
                    help="ISO timestamp, or shorthand like 7d / 48h")
    ap.add_argument("--last", dest="since", help=argparse.SUPPRESS)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    hdir = os.path.join(a.root, "health")
    g = digest(load(os.path.join(hdir, "health.jsonl")),
               load(os.path.join(hdir, "alarms.jsonl")),
               parse_since(a.since))
    if g is None:
        print("Not enough health samples yet for a digest.", file=sys.stderr)
        return 2

    leaked = leaks(g)
    if leaked:
        print(f"REFUSING: blinding leak {leaked}", file=sys.stderr)
        return 3

    print(json.dumps(g, indent=2) if a.json else render(g))
    return 0


if __name__ == "__main__":
    sys.exit(main())
