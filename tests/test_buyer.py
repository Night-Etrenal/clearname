"""Smoke tests for the ClearName demo buyer (D4).

End-to-end through the real CLI in a subprocess, from the repo root,
exactly as the README's "do X, see Y" steps prescribe. All scenarios
are fixtures (no network, no live hiring, no payment).

Runs two ways (same assertions either way):

    python3 tests/test_buyer.py
    python3 -m pytest tests/test_buyer.py -q
"""

import json
import os
import subprocess
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(REPO_ROOT, "engine", "fixtures")


def _run(args):
    env = dict(os.environ)
    env.pop("CLEARNAME_AGENT_API_TOKEN", None)
    env.pop("CLEARNAME_MCP_ENDPOINT", None)
    return subprocess.run(
        [sys.executable, "-m", "buyer.demo_buyer"] + args,
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=60)


def _verdict(out):
    with open(os.path.join(out, "verdict.json"), encoding="utf-8") as f:
        return json.load(f)


def test_buyer_zenvora_blocked_requests_variants():
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "run")
        proc = _run(["--scenario", "zenvora", "--out-dir", out])
        assert proc.returncode == 0, proc.stderr
        assert "雇佣 ClearName" in proc.stdout, proc.stdout
        assert "verdict=BLOCKED" in proc.stdout, proc.stdout
        assert "请求变体建议" in proc.stdout, proc.stdout
        assert "不验收" in proc.stdout
        assert _verdict(out)["verdict"] == "BLOCKED"


def test_buyer_clear_accepts():
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "run")
        proc = _run(["--scenario", "clear", "--out-dir", out])
        assert proc.returncode == 0, proc.stderr
        assert "verdict=CLEAR" in proc.stdout, proc.stdout
        assert "验收通过" in proc.stdout, proc.stdout
        assert _verdict(out)["verdict"] == "CLEAR"


def test_buyer_caution_holds_for_review():
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "run")
        proc = _run(["--result-json", os.path.join(FIXTURES, "caution.json"),
                     "--out-dir", out])
        assert proc.returncode == 0, proc.stderr
        assert "verdict=CAUTION" in proc.stdout, proc.stdout
        assert "人工复核" in proc.stdout, proc.stdout
        assert _verdict(out)["verdict"] == "CAUTION"


def test_buyer_bad_brief_refuses():
    with tempfile.TemporaryDirectory() as tmp:
        payload_path = os.path.join(tmp, "bad.json")
        with open(payload_path, "w", encoding="utf-8") as f:
            json.dump({"name": "", "result": {}}, f)
        proc = _run(["--result-json", payload_path,
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
