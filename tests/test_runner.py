"""Smoke tests for the ClearName runtime (D3).

End-to-end through the real CLI in a subprocess, from the repo root,
exactly as the README's "do X, see Y" steps prescribe. Dry-run tests
use fixture assignments only (no network, no accounts, no submission).
The live-mode test pins the D3 refusal: without the live path
implemented, the runner must fail loudly, never pretend.

Runs two ways (same assertions either way):

    python3 tests/test_runner.py
    python3 -m pytest tests/test_runner.py -q
"""

import json
import os
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(args, env_extra=None):
    env = dict(os.environ)
    env.pop("CLEARNAME_AGENT_API_TOKEN", None)
    env.pop("CLEARNAME_MCP_ENDPOINT", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "agent.runner"] + args,
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=60)


def _clear_result():
    return {
        "name": "clearname-demo",
        "result": {
            "checked": "2026-11-02T00:00:00+08:00",
            "domains": [{"domain": "clearname-demo.com", "status": "available",
                         "evidence": "RDAP 404", "checked": "t"}],
            "socials": [{"platform": "x", "handle": "clearname-demo",
                         "status": "available", "evidence": "no account",
                         "url": "https://x.com/clearname-demo", "checked": "t"}],
            "itunes": [], "itunes_note": "iTunes Search API, 0 result(s)",
            "play": [], "play_note": "public Play search page",
            "trademarks": {"available_source": True, "checked": "t",
                           "marks": [], "total": 0, "coverage": "c"},
        },
    }


def test_dry_run_zenvora_blocked():
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "run")
        proc = _run(["--dry-run", "--scenario", "zenvora", "--out-dir", out])
        assert proc.returncode == 0, proc.stderr
        assert "verdict: BLOCKED" in proc.stdout, proc.stdout
        assert "report:" in proc.stdout and out in proc.stdout
        with open(os.path.join(out, "report.md"), encoding="utf-8") as f:
            md = f.read()
        assert "Verdict: BLOCKED" in md
        with open(os.path.join(out, "verdict.json"), encoding="utf-8") as f:
            v = json.load(f)
        assert v["verdict"] == "BLOCKED"
        assert v["schema_version"] == "clearname-verdict-v1"


def test_dry_run_clear():
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "run")
        proc = _run(["--dry-run", "--scenario", "clear", "--out-dir", out])
        assert proc.returncode == 0, proc.stderr
        assert "verdict: CLEAR" in proc.stdout, proc.stdout
        with open(os.path.join(out, "verdict.json"), encoding="utf-8") as f:
            v = json.load(f)
        assert v["verdict"] == "CLEAR"
        assert v["risk_score"] == 0


def test_dry_run_custom_result_json():
    with tempfile.TemporaryDirectory() as tmp:
        payload_path = os.path.join(tmp, "custom.json")
        with open(payload_path, "w", encoding="utf-8") as f:
            json.dump(_clear_result(), f)
        out = os.path.join(tmp, "run")
        proc = _run(["--dry-run", "--result-json", payload_path,
                     "--out-dir", out])
        assert proc.returncode == 0, proc.stderr
        assert "verdict: CLEAR" in proc.stdout, proc.stdout


def test_live_mode_refuses_loudly():
    proc = _run(["--live"])
    assert proc.returncode != 0
    combined = proc.stdout + proc.stderr
    assert "not implemented in D3" in combined, combined
    assert "CLEARNAME_AGENT_API_TOKEN" in combined
    # Refusal happens before any delivery directory is created.
    assert "report:" not in proc.stdout


def test_unknown_scenario_refuses():
    proc = _run(["--dry-run", "--scenario", "nope"])
    assert proc.returncode != 0


def test_bad_brief_refuses():
    with tempfile.TemporaryDirectory() as tmp:
        payload_path = os.path.join(tmp, "bad.json")
        payload = _clear_result()
        payload["name"] = ""
        with open(payload_path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        proc = _run(["--dry-run", "--result-json", payload_path,
                     "--out-dir", os.path.join(tmp, "run")])
        assert proc.returncode != 0
        assert "refused" in (proc.stdout + proc.stderr).lower()


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
