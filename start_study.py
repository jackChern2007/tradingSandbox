#!/usr/bin/env python3
"""
start_study.py - write study_start.json exactly once, at launch.

This is the timestamp the cold-start alarm is measured from. Without it a
harness that never starts is silent forever. It also pins the run's config so
healthcheck.py can detect drift in model hash, prompt version and sampling
parameters without being told them on every invocation.

    python3 start_study.py --root study --pin <model_sha256> \
        --prompt-version v3 --interval 600 --temperature 0 --top-p 1 --seed 1234

Refuses to overwrite. A new sample gets a new root.
"""

import argparse, json, os, subprocess, sys
from datetime import datetime, timezone

START_FILE = "study_start.json"


def git(*args):
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        return subprocess.run(["git", *args], cwd=here, capture_output=True,
                              text=True, timeout=5).stdout.strip() or None
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="study")
    ap.add_argument("--pin", required=True, help="sha256 of the weights file")
    ap.add_argument("--prompt-version", required=True)
    ap.add_argument("--interval", type=int, required=True,
                    help="expected seconds between cycles")
    ap.add_argument("--temperature", type=float, default=None)
    ap.add_argument("--top-p", type=float, default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--note", default=None)
    a = ap.parse_args()

    if len(a.pin) != 64 or any(c not in "0123456789abcdef" for c in a.pin.lower()):
        print("--pin must be a 64-hex sha256", file=sys.stderr)
        return 2

    path = os.path.join(a.root, START_FILE)
    if os.path.exists(path):
        print(f"REFUSING: {path} already exists. A study starts once.",
              file=sys.stderr)
        return 1

    for d in ("forecasts", "forecasts/context", "health",
              "devlog/entries", "devlog/posts"):
        os.makedirs(os.path.join(a.root, d), exist_ok=True)

    rec = {
        "study_start_ts": datetime.now(timezone.utc).isoformat(),
        "model_sha256": a.pin.lower(),
        "prompt_version": a.prompt_version,
        "expected_interval_s": a.interval,
        "protocol_commit": git("rev-parse", "HEAD"),
        "protocol_tag": git("describe", "--tags", "--exact-match"),
        "note": a.note,
    }
    for k in ("temperature", "top_p", "seed"):
        v = getattr(a, k)
        if v is not None:
            rec[k] = v

    with open(path, "x") as fh:
        json.dump(rec, fh, indent=2)
        fh.write("\n")
        fh.flush(); os.fsync(fh.fileno())
    print(json.dumps(rec, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
