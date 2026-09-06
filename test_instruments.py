#!/usr/bin/env python3
"""
test_instruments.py - acceptance tests for the pre-launch instruments.

    python3 test_instruments.py

Exercises the scripts through their CLIs, exactly as they will be run. Covers:
fixtures behave as labelled (null spans zero, worse/overconf caught, retry
refused); the forecast/resolution split; the resolved count in health output;
every T5 health invariant (clock, cold start, schema, pin, sustained
escalation); and the devlog leak guard. Stdlib only.
"""

import json, os, shutil, subprocess, sys, tempfile, unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import make_synthetic as ms  # noqa: E402

PIN = ms.PIN
INTERVAL = 600
N = 320
# with per_cycle=2 the fixture spans 160 cycles; this start puts the last
# cycle ~5 minutes ago, so the fixture is "live"
LIVE_START_AGO = (N // 2 - 1) * INTERVAL + 300


def run(script, *args, cwd=None):
    p = subprocess.run([sys.executable, os.path.join(HERE, script), *args],
                       cwd=cwd, capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def rewrite(path, fn):
    """Apply fn(record) -> record|None to every line of a jsonl file."""
    out = []
    with open(path) as fh:
        for line in fh:
            if line.strip():
                r = fn(json.loads(line))
                if r is not None:
                    out.append(json.dumps(r))
    with open(path, "w") as fh:
        fh.write("\n".join(out) + "\n")


class Study:
    def __init__(self, tmp, start_ago_s, fixture=True, start_file=True,
                 scenario="null", **gen_kw):
        self.root = os.path.join(tmp, "study")
        self.fdir = os.path.join(self.root, "forecasts")
        self.hdir = os.path.join(self.root, "health")
        os.makedirs(self.fdir); os.makedirs(self.hdir)
        self.fpath = os.path.join(self.fdir, "forecasts.jsonl")
        self.rpath = os.path.join(self.fdir, "resolutions.jsonl")
        self.hpath = os.path.join(self.hdir, "health.jsonl")
        self.t0 = datetime.now(timezone.utc) - timedelta(seconds=start_ago_s)
        if start_file:
            with open(os.path.join(self.root, "study_start.json"), "w") as fh:
                json.dump({"study_start_ts": self.t0.isoformat(),
                           "model_sha256": PIN, "prompt_version": "v3",
                           "expected_interval_s": INTERVAL,
                           "temperature": 0.0, "top_p": 1.0, "seed": 1234}, fh)
        if fixture:
            ms.gen(self.fpath, scenario, n=N, start=self.t0,
                   interval_s=INTERVAL, **gen_kw)
            os.replace(self.fpath[:-6] + ".resolutions.jsonl", self.rpath)

    def health(self, *args):
        rc, out, err = run("healthcheck.py", "--root", self.root, *args)
        js = json.loads(out) if out.strip().startswith("{") else None
        return rc, js, err

    def seed_history(self, samples):
        with open(self.hpath, "a") as fh:
            for s in samples:
                fh.write(json.dumps(s) + "\n")


class Fixtures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        rc, out, err = run("make_synthetic.py", cwd=cls.tmp)
        assert rc == 0, err

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def score(self, name, *extra):
        return run("score.py", f"syn_{name}.jsonl", "--min-n", "300",
                   "--boot", "4000", *extra, cwd=self.tmp)

    def test_null_spans_zero(self):
        rc, out, _ = self.score("null")
        self.assertEqual(rc, 0)
        self.assertIn("No detectable difference; CI spans zero", out)
        self.assertIn("Resolutions: 320 markets", out)

    def test_worse_is_caught(self):
        rc, out, _ = self.score("worse")
        self.assertEqual(rc, 0)
        self.assertIn("Market beats the model; CI excludes zero", out)

    def test_overconf_is_caught(self):
        rc, out, _ = self.score("overconf")
        self.assertIn("Market beats the model; CI excludes zero", out)

    def test_edge_undetectable_at_300(self):
        rc, out, _ = self.score("edge")
        self.assertIn("CI spans zero", out)

    def test_retry_refuses_to_score(self):
        rc, out, _ = self.score("retry")
        self.assertEqual(rc, 3)
        self.assertIn("REFUSING TO SCORE", out)
        self.assertNotIn("PRIMARY RESULT", out)

    def test_parse_failure_denominator_is_all_attempts(self):
        _, out, _ = self.score("null")
        self.assertIn("of 320 forecast attempts", out)

    def test_outcome_inside_forecast_is_rejected(self):
        src = os.path.join(self.tmp, "syn_null.jsonl")
        dst = os.path.join(self.tmp, "leak.jsonl")
        shutil.copy(src, dst)
        shutil.copy(src[:-6] + ".resolutions.jsonl", dst[:-6] + ".resolutions.jsonl")
        n = {"i": 0}

        def poison(r):
            n["i"] += 1
            if n["i"] == 3:
                r["outcome"] = 1
            return r
        rewrite(dst, poison)
        rc, out, _ = run("score.py", "leak.jsonl", "--boot", "500", cwd=self.tmp)
        self.assertIn("1  outcome field inside forecast record", out)

    def test_conflicting_resolution_is_unscoreable(self):
        src = os.path.join(self.tmp, "syn_null.jsonl")
        dst = os.path.join(self.tmp, "conf.jsonl")
        shutil.copy(src, dst)
        rp = dst[:-6] + ".resolutions.jsonl"
        shutil.copy(src[:-6] + ".resolutions.jsonl", rp)
        with open(rp) as fh:
            first = json.loads(fh.readline())
        first["outcome"] = 1 - first["outcome"]
        with open(rp, "a") as fh:
            fh.write(json.dumps(first) + "\n")
        rc, out, _ = run("score.py", "conf.jsonl", "--boot", "500", cwd=self.tmp)
        self.assertIn("1 conflicting", out)
        self.assertIn("1  conflicting resolutions for market", out)

    def test_unresolved_is_not_an_error(self):
        src = os.path.join(self.tmp, "syn_null.jsonl")
        dst = os.path.join(self.tmp, "nores.jsonl")
        shutil.copy(src, dst)
        rc, out, _ = run("score.py", "nores.jsonl", "--boot", "500", cwd=self.tmp)
        self.assertEqual(rc, 0)
        self.assertIn("not yet resolved", out)
        self.assertIn("Nothing to score yet", out)


class Health(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_healthy_run_and_devlog(self):
        s = Study(self.tmp, LIVE_START_AGO)
        rc, h, err = s.health()
        self.assertEqual(rc, 0, err)
        self.assertNotIn("[HALT]", err)
        self.assertEqual(h["resolved_count"], N)
        self.assertGreater(h["resolved_forecasts"], 250)
        self.assertEqual(h["schema_violations"], 0)
        self.assertEqual(h["clock_violations"], 0)
        self.assertTrue(h["model_sha256_ok"])
        for k in h:
            for f in ("outcome", "brier", "accuracy"):
                self.assertNotIn(f, k)
        s.health()
        rc, out, err = run("devlog.py", "--root", s.root, "--since", "1d")
        self.assertEqual(rc, 0, err)
        self.assertIn(f"resolved forecasts: {h['resolved_forecasts']} of 300", out)
        self.assertNotIn("brier", out.lower())

    def test_cli_pin_mismatch_halts(self):
        s = Study(self.tmp, LIVE_START_AGO)
        rc, h, err = s.health("--pin", "b" * 64)
        self.assertEqual(rc, 1)
        self.assertIn("config_mismatch", err)

    def test_missing_start_file_halts(self):
        s = Study(self.tmp, LIVE_START_AGO, start_file=False)
        rc, h, err = s.health("--pin", PIN, "--interval", str(INTERVAL))
        self.assertEqual(rc, 1)
        self.assertIn("study_start_missing", err)

    def test_cold_start_halts_after_grace(self):
        s = Study(self.tmp, 4 * INTERVAL, fixture=False)
        rc, h, err = s.health()
        self.assertEqual(rc, 1)
        self.assertIn("[HALT] cold_start", err)

    def test_cold_start_silent_within_grace(self):
        s = Study(self.tmp, INTERVAL, fixture=False)
        rc, h, err = s.health()
        self.assertEqual(rc, 0, err)
        self.assertEqual(h["records_total"], 0)

    def test_future_timestamps_halt(self):
        s = Study(self.tmp, 0)          # cycles run into the future
        rc, h, err = s.health()
        self.assertEqual(rc, 1)
        self.assertIn("[HALT] clock", err)
        self.assertGreater(h["clock_violations"], 0)

    def test_non_monotonic_cycles_halt(self):
        s = Study(self.tmp, LIVE_START_AGO)
        with open(s.fpath) as fh:
            lines = fh.readlines()
        lines[10], lines[100] = lines[100], lines[10]
        with open(s.fpath, "w") as fh:
            fh.writelines(lines)
        rc, h, err = s.health()
        self.assertEqual(rc, 1)
        self.assertIn("non-monotonic", err)

    def test_dead_harness_halts(self):
        s = Study(self.tmp, LIVE_START_AGO + 4 * INTERVAL)
        rc, h, err = s.health()
        self.assertEqual(rc, 1)
        self.assertIn("[HALT] harness_alive", err)

    def test_schema_missing_field_halts(self):
        s = Study(self.tmp, LIVE_START_AGO)
        n = {"i": 0}

        def drop(r):
            n["i"] += 1
            if n["i"] == 7:
                del r["parse_ok"]
            return r
        rewrite(s.fpath, drop)
        rc, h, err = s.health()
        self.assertEqual(rc, 1)
        self.assertIn("[HALT] schema", err)
        self.assertIn("missing parse_ok", err)

    def test_schema_wrong_type_halts(self):
        s = Study(self.tmp, LIVE_START_AGO)
        rewrite(s.fpath, lambda r: dict(r, attempt="1"))
        rc, h, err = s.health()
        self.assertEqual(rc, 1)
        self.assertIn("wrong type for attempt", err)

    def test_outcome_in_forecast_halts(self):
        s = Study(self.tmp, LIVE_START_AGO)
        n = {"i": 0}

        def leak(r):
            n["i"] += 1
            return dict(r, outcome=1) if n["i"] == 2 else r
        rewrite(s.fpath, leak)
        rc, h, err = s.health()
        self.assertEqual(rc, 1)
        self.assertIn("[HALT] schema", err)

    def test_corrupt_middle_line_halts_partial_last_line_reports(self):
        s = Study(self.tmp, LIVE_START_AGO)
        with open(s.fpath, "a") as fh:
            fh.write('{"record_id": "torn", "cycle_')
        rc, h, err = s.health()
        self.assertEqual(rc, 0, err)
        self.assertIn("[REPORT] partial_write", err)
        with open(s.fpath) as fh:
            lines = fh.readlines()
        lines[5] = "{not json}\n"
        with open(s.fpath, "w") as fh:
            fh.writelines(lines)
        rc, h, err = s.health()
        self.assertEqual(rc, 1)
        self.assertIn("unparseable line", err)

    def test_retry_halts(self):
        s = Study(self.tmp, LIVE_START_AGO, inject_retry=True)
        rc, h, err = s.health()
        self.assertEqual(rc, 1)
        self.assertIn("[HALT] retry_discipline", err)

    def test_prompt_version_drift_halts(self):
        s = Study(self.tmp, LIVE_START_AGO, prompt_version="v2")
        rc, h, err = s.health()
        self.assertEqual(rc, 1)
        self.assertIn("prompt_version differs", err)

    def test_sustained_report_escalates_to_halt(self):
        s = Study(self.tmp, LIVE_START_AGO)
        rewrite(s.fpath, lambda r: dict(
            r, agent_p_verbalized=0.5 if r["parse_ok"] else None))
        rc, h, err = s.health()
        self.assertEqual(rc, 0, err)
        self.assertIn("[REPORT] no_discrimination", err)
        self.assertIn("no_discrimination", h["active_reports"])
        rc, h, err = s.health()
        self.assertEqual(rc, 0, err)          # 2 samples: still a report
        rc, h, err = s.health()
        self.assertEqual(rc, 1)               # 3rd consecutive: halt
        self.assertIn("[HALT] no_discrimination", err)
        self.assertIn("sustained 3 consecutive", err)

    def test_report_clears_then_no_escalation(self):
        s = Study(self.tmp, LIVE_START_AGO)
        s.seed_history([
            {"ts": (s.t0 + timedelta(seconds=k)).isoformat(),
             "active_reports": ["no_discrimination"] if k != 200 else [],
             "mean_retrieved_chars": 4000, "thin_evidence_rate": 0.3}
            for k in (100, 200)])
        rewrite(s.fpath, lambda r: dict(
            r, agent_p_verbalized=0.5 if r["parse_ok"] else None))
        rc, h, err = s.health()
        self.assertEqual(rc, 0, err)
        self.assertIn("[REPORT] no_discrimination", err)


class Devlog(unittest.TestCase):
    def test_refuses_forbidden_key(self):
        tmp = tempfile.mkdtemp()
        try:
            hdir = os.path.join(tmp, "study", "health")
            os.makedirs(hdir)
            now = datetime.now(timezone.utc)
            with open(os.path.join(hdir, "health.jsonl"), "w") as fh:
                for k in (2, 1):
                    fh.write(json.dumps({
                        "ts": (now - timedelta(hours=k)).isoformat(),
                        "cycles_total": 1, "records_total": 1,
                        "parse_failures_by_reason": {"brier_leak": 3 - k}}) + "\n")
            rc, out, err = run("devlog.py", "--root",
                               os.path.join(tmp, "study"), "--since", "1d")
            self.assertEqual(rc, 3)
            self.assertIn("REFUSING", err)
        finally:
            shutil.rmtree(tmp)


class StartStudy(unittest.TestCase):
    def test_writes_once_then_refuses(self):
        tmp = tempfile.mkdtemp()
        try:
            root = os.path.join(tmp, "study")
            args = ["--root", root, "--pin", PIN, "--prompt-version", "v3",
                    "--interval", "600", "--seed", "1234"]
            rc, out, err = run("start_study.py", *args)
            self.assertEqual(rc, 0, err)
            with open(os.path.join(root, "study_start.json")) as fh:
                st = json.load(fh)
            for k in ("study_start_ts", "model_sha256", "prompt_version",
                      "expected_interval_s", "seed"):
                self.assertIn(k, st)
            self.assertTrue(os.path.isdir(os.path.join(root, "forecasts", "context")))
            rc, out, err = run("start_study.py", *args)
            self.assertEqual(rc, 1)
            self.assertIn("REFUSING", err)
            rc, out, err = run("healthcheck.py", "--root", root)
            self.assertEqual(rc, 0, err)       # within grace, no forecasts yet
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main(verbosity=2)
