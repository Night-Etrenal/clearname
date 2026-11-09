"""ClearName self-hosted guardrails (D8 content, landed early).

Why early: the D5 range-freeze gate (2026-11-05) decides stretch
vs. cut-to-hardening, but guardrails are needed on EITHER branch —
they protect the runtime itself, not a stretch feature. So this module
lands before D5; the live-path wiring that enforces it arrives with
the live path (after the user's browser steps + D5 gate).

What it enforces (all failures are loud, never silent downgrades):

- Brief validation: same rules as the engine — non-empty name, max
  100 chars, no control characters, jurisdiction allowlist (only
  "US" today, because only the USPTO register is implemented).
- Price band: a paid job is accepted only inside an operator-set
  [min, max] band (USDC). If no band is configured and a price IS
  supplied, the job is refused (fail closed): accepting paid work
  with no band is how a runtime gets hired below cost. Fixture jobs
  carry no price and are unaffected.
- Daily job cap: at most ``daily_job_cap`` accepted jobs per UTC
  date, counted in a JSON state file. A corrupt state file fails
  closed (refuse + explicit error) rather than silently resetting
  the count — the operator deletes the file deliberately to reset.
- Single-job timeout: ``run_with_timeout`` runs a callable with a
  wall-clock budget and reports timeout instead of hanging.
- Log integrity: append-only JSONL event log where every line
  carries the SHA-256 of the previous line (hash chain). Any edit,
  deletion, or reorder breaks ``verify_log`` at the first bad line.

This module performs no network I/O and never touches an account.
"""

import concurrent.futures
import hashlib
import json
import math
import os
from datetime import datetime, timezone

DEFAULT_DAILY_JOB_CAP = 10
DEFAULT_JOB_TIMEOUT_SECONDS = 300
ALLOWED_JURISDICTIONS = ("US",)


class GuardrailViolation(Exception):
    """A job or action the guardrails refuse."""


def _is_finite_number(value):
    """True only for a real, finite, non-bool number.

    math.isfinite raises OverflowError on gigantic ints instead of
    returning False, so a guardrail must not call it unguarded: an
    unhandled exception is not a refusal.
    """
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def validate_brief(name, jurisdiction="US"):
    """Validate a brief; raise GuardrailViolation on anything uncheckable."""
    if not name or not isinstance(name, str) or not name.strip():
        raise GuardrailViolation("brief has no usable name")
    if len(name) > 100:
        raise GuardrailViolation(
            f"brand name too long ({len(name)} chars, max 100)")
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise GuardrailViolation("brand name contains control characters")
    if jurisdiction not in ALLOWED_JURISDICTIONS:
        raise GuardrailViolation(
            f"jurisdiction {jurisdiction!r} not supported: only "
            f"{', '.join(ALLOWED_JURISDICTIONS)} implemented")


class Guardrails:
    """Operator-configured limits for the self-hosted runtime."""

    def __init__(self, state_path, daily_job_cap=DEFAULT_DAILY_JOB_CAP,
                 job_timeout_seconds=DEFAULT_JOB_TIMEOUT_SECONDS,
                 min_price_usdc=None, max_price_usdc=None):
        self.state_path = state_path
        self.daily_job_cap = daily_job_cap
        self.job_timeout_seconds = job_timeout_seconds
        self.min_price_usdc = min_price_usdc
        self.max_price_usdc = max_price_usdc
        for bound in (min_price_usdc, max_price_usdc):
            if bound is not None and (not _is_finite_number(bound)
                                      or bound < 0):
                raise GuardrailViolation(
                    "price band bounds must be finite, non-negative numbers")
        if (min_price_usdc is not None and max_price_usdc is not None
                and min_price_usdc > max_price_usdc):
            raise GuardrailViolation(
                "price band minimum exceeds maximum")

    # ---- price band ----
    def check_price(self, price_usdc):
        """Validate a job price; None means 'no price supplied' (fixture)."""
        if price_usdc is None:
            return
        if not _is_finite_number(price_usdc):
            raise GuardrailViolation(
                "job price must be a finite number")
        if self.min_price_usdc is None and self.max_price_usdc is None:
            raise GuardrailViolation(
                "price band not configured — refusing paid job "
                "without an operator-set band")
        if (self.min_price_usdc is not None
                and price_usdc < self.min_price_usdc):
            raise GuardrailViolation(
                f"job price {price_usdc} USDC below band minimum "
                f"{self.min_price_usdc} USDC")
        if (self.max_price_usdc is not None
                and price_usdc > self.max_price_usdc):
            raise GuardrailViolation(
                f"job price {price_usdc} USDC above band maximum "
                f"{self.max_price_usdc} USDC")

    # ---- daily cap ----
    def _today(self):
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _read_state(self):
        if not os.path.exists(self.state_path):
            return {}
        try:
            with open(self.state_path, encoding="utf-8") as f:
                state = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            raise GuardrailViolation(
                f"guardrail state file unreadable ({self.state_path}): "
                f"{exc} — refusing rather than silently resetting counts")
        if not isinstance(state, dict):
            raise GuardrailViolation(
                f"guardrail state file malformed ({self.state_path}): "
                "top level is not an object — refusing")
        return state

    def _write_state(self, state):
        parent = os.path.dirname(os.path.abspath(self.state_path))
        os.makedirs(parent, exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        os.replace(tmp, self.state_path)

    def check_and_record(self, date=None):
        """Enforce the daily cap; record one accepted job. Returns count."""
        date = date or self._today()
        state = self._read_state()
        count = state.get(date, 0)
        if count >= self.daily_job_cap:
            raise GuardrailViolation(
                f"daily job cap reached ({self.daily_job_cap} on {date})")
        state[date] = count + 1
        self._write_state(state)
        return state[date]

    # ---- timeout ----
    def run_with_timeout(self, fn, *args, **kwargs):
        """Run fn with the job timeout; returns (ok, result_or_None)."""
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(fn, *args, **kwargs)
            try:
                return True, future.result(timeout=self.job_timeout_seconds)
            except concurrent.futures.TimeoutError:
                return False, None


# ---- hash-chained event log ----
def _line_hash(prev_hash, payload):
    blob = (prev_hash + "\n" + json.dumps(payload, sort_keys=True,
                                           ensure_ascii=False)).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def append_event(log_path, event):
    """Append one event dict to a hash-chained JSONL log. Returns the hash."""
    if not isinstance(event, dict):
        raise GuardrailViolation("log event must be an object")
    prev_hash = "GENESIS"
    if os.path.exists(log_path):
        with open(log_path, encoding="utf-8") as f:
            lines = [ln for ln in f if ln.strip()]
        if lines:
            prev_hash = json.loads(lines[-1])["hash"]
    payload = dict(event)
    payload["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    line_hash = _line_hash(prev_hash, payload)
    record = {"prev_hash": prev_hash, "hash": line_hash, "event": payload}
    parent = os.path.dirname(os.path.abspath(log_path))
    os.makedirs(parent, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return line_hash


def verify_log(log_path):
    """Verify a hash-chained log. Returns (ok, first_bad_line_number|None)."""
    if not os.path.exists(log_path):
        return True, None
    prev_hash = "GENESIS"
    with open(log_path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                return False, lineno
            if record.get("prev_hash") != prev_hash:
                return False, lineno
            if record.get("hash") != _line_hash(prev_hash, record.get("event")):
                return False, lineno
            prev_hash = record["hash"]
    return True, None
