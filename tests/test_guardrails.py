"""Tests for the ClearName guardrails (D8 content, landed early).

Offline, no accounts, no network. The daily-cap state and the event
log live in temp dirs. The runner-wiring tests drive the real CLI.

Runs two ways (same assertions either way):

    python3 tests/test_guardrails.py
    python3 -m pytest tests/test_guardrails.py -q
"""

import json
import os
import subprocess
import sys
import tempfile
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "agent"))

from guardrails import (GuardrailViolation, Guardrails, append_event,
                        validate_brief, verify_log)


def _expect_violation(fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except GuardrailViolation:
        return
    raise AssertionError(f"expected GuardrailViolation from {fn.__name__}")


def test_validate_brief_ok():
    validate_brief("zenvora", "US")


def test_validate_brief_rejects():
    _expect_violation(validate_brief, "", "US")
    _expect_violation(validate_brief, "x" * 101, "US")
    _expect_violation(validate_brief, "bad\x01name", "US")
    _expect_violation(validate_brief, "zenvora", "EU")


def test_price_band():
    with tempfile.TemporaryDirectory() as tmp:
        state = os.path.join(tmp, "state.json")
        g = Guardrails(state)
        g.check_price(None)  # fixture jobs carry no price
        _expect_violation(g.check_price, 15.0)  # no band -> fail closed
        g2 = Guardrails(state, min_price_usdc=10.0, max_price_usdc=100.0)
        g2.check_price(15.0)
        _expect_violation(g2.check_price, 5.0)
        _expect_violation(g2.check_price, 500.0)


def test_daily_cap_and_persistence():
    with tempfile.TemporaryDirectory() as tmp:
        state = os.path.join(tmp, "state.json")
        g = Guardrails(state, daily_job_cap=2)
        assert g.check_and_record(date="2026-11-02") == 1
        assert g.check_and_record(date="2026-11-02") == 2
        _expect_violation(g.check_and_record, date="2026-11-02")
        # A fresh instance on the same file still sees the cap.
        g2 = Guardrails(state, daily_job_cap=2)
        _expect_violation(g2.check_and_record, date="2026-11-02")
        # Another date is unaffected.
        assert g2.check_and_record(date="2026-11-03") == 1


def test_corrupt_state_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:
        state = os.path.join(tmp, "state.json")
        with open(state, "w", encoding="utf-8") as f:
            f.write("{not json")
        g = Guardrails(state)
        _expect_violation(g.check_and_record, date="2026-11-02")


def test_run_with_timeout():
    with tempfile.TemporaryDirectory() as tmp:
        g = Guardrails(os.path.join(tmp, "state.json"),
                       job_timeout_seconds=5)
        ok, res = g.run_with_timeout(lambda: 42)
        assert (ok, res) == (True, 42)
        g_fast_fail = Guardrails(os.path.join(tmp, "state.json"),
                                 job_timeout_seconds=0.1)
        ok, res = g_fast_fail.run_with_timeout(time.sleep, 0.5)
        assert ok is False and res is None


def test_log_chain_verifies_and_detects_tamper():
    with tempfile.TemporaryDirectory() as tmp:
        log = os.path.join(tmp, "events.jsonl")
        append_event(log, {"job": "a"})
        append_event(log, {"job": "b"})
        append_event(log, {"job": "c"})
        ok, bad = verify_log(log)
        assert (ok, bad) == (True, None)
        with open(log, encoding="utf-8") as f:
            lines = f.readlines()
        tampered = lines[1].replace('"b"', '"B"')
        assert tampered != lines[1]
        lines[1] = tampered
        with open(log, "w", encoding="utf-8") as f:
            f.writelines(lines)
        ok, bad = verify_log(log)
        assert ok is False and bad == 2


def test_runner_guardrails_wiring():
    with tempfile.TemporaryDirectory() as tmp:
        state = os.path.join(tmp, "state.json")
        out = os.path.join(tmp, "run")
        env = dict(os.environ)
        proc = subprocess.run(
            [sys.executable, "-m", "agent.runner", "--dry-run",
             "--scenario", "clear", "--out-dir", out,
             "--guardrails-state", state],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True,
            timeout=60)
        assert proc.returncode == 0, proc.stderr
        assert "guardrails: brief ok" in proc.stdout, proc.stdout
        with open(state, encoding="utf-8") as f:
            counts = json.load(f)
        assert sum(counts.values()) == 1
        # A priced job with no band configured must be refused.
        proc2 = subprocess.run(
            [sys.executable, "-m", "agent.runner", "--dry-run",
             "--scenario", "clear", "--out-dir", os.path.join(tmp, "run2"),
             "--guardrails-state", state, "--price-usdc", "5"],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True,
            timeout=60)
        assert proc2.returncode != 0
        assert "guardrail refused" in (proc2.stdout + proc2.stderr).lower()


def _all_tests():
    return [(name, fn) for name, fn in sorted(globals().items())
            if name.startswith("test_") and callable(fn)]


if __name__ == "__main__":
    failures = 0
    for name, fn in _all_tests():
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - plain runner reports all
            failures += 1
            print(f"FAIL {name}: {exc}")
        else:
            print(f"PASS {name}")
    print(f"{len(_all_tests()) - failures}/{len(_all_tests())} passed")
    raise SystemExit(1 if failures else 0)
