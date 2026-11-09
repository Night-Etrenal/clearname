"""ClearName self-hosted runtime v1 (D3, NEAR HACKATHON, 2026-11-02).

Job flow for one assignment: parse the brief (name + jurisdiction) ->
aggregate the engine result through ``verdict.py`` -> render the dual
delivery (Markdown + JSON) -> hand the delivery over.

D3 scope, stated plainly: the end-to-end path runs on **fixture
assignments only** (``--dry-run``). The live path — polling the NEAR
market over MCP, calling ``marketplace__submit_deliverable``, messaging
the buyer — is NOT implemented in D3 and the runner refuses it loudly
instead of faking it. Live prerequisites (all still pending, all owned
by the user in their browser):

1. the Brand Checker market listing switched to self-hosted;
2. an agent API token, supplied only via the ``CLEARNAME_AGENT_API_TOKEN``
   environment variable (never in the repo, never on the command line);
3. a reachable MCP endpoint (``CLEARNAME_MCP_ENDPOINT``).

Dry-run guarantees: no network I/O, no account access, no submission,
no money movement. The dry-run path never imports the engine's live
checkers; it only aggregates result dicts (fixtures or ``--result-json``).

Usage (from the repo root):

    python -m agent.runner --dry-run --scenario zenvora
    python -m agent.runner --dry-run --scenario clear
    python -m agent.runner --dry-run --result-json path/to/result.json
    python -m agent.runner --live   # refuses until D3-live lands

Input validation mirrors the engine's own rules: empty names, names
over 100 chars, control characters, and non-US jurisdictions (the
engine implements only the USPTO register) are rejected with a
non-zero exit instead of producing a verdict.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ENGINE_DIR = os.path.join(_REPO_ROOT, "engine")
for _p in (_REPO_ROOT, _ENGINE_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from verdict import verdict_for  # noqa: E402
from render import render_json, render_markdown  # noqa: E402

SCENARIOS = ("zenvora", "clear")
SUPPORTED_JURISDICTIONS = ("US",)


class JobError(Exception):
    """A job that must not produce a verdict (bad brief, bad source)."""


def parse_brief(brief):
    """Validate a job brief dict and return (name, jurisdiction).

    Raises JobError on anything the engine cannot honestly check.
    """
    if not isinstance(brief, dict):
        raise JobError("brief must be an object with name + jurisdiction")
    name = brief.get("name")
    jurisdiction = brief.get("jurisdiction", "US")
    if not name or not isinstance(name, str) or not name.strip():
        raise JobError("brief has no usable name")
    if len(name) > 100:
        raise JobError(f"brand name too long ({len(name)} chars, max 100)")
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise JobError("brand name contains control characters")
    if jurisdiction not in SUPPORTED_JURISDICTIONS:
        raise JobError(
            f"jurisdiction {jurisdiction!r} not supported: only the US "
            "(USPTO) register is implemented — other registers would be "
            "couldn't confirm, not a verdict")
    return name.strip(), jurisdiction


def load_scenario(scenario):
    """Load a fixture assignment: returns (brief, engine result dict)."""
    if scenario not in SCENARIOS:
        raise JobError(f"unknown scenario {scenario!r} (expected one of "
                       f"{', '.join(SCENARIOS)})")
    path = os.path.join(_ENGINE_DIR, "fixtures", scenario + ".json")
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    brief = {"name": payload["name"], "jurisdiction": "US",
             "source": f"fixture:{scenario}"}
    return brief, payload["result"]


def load_result_json(path):
    """Load a custom engine result: {"name": str, "result": {...}}."""
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    brief = {"name": payload["name"], "jurisdiction": "US",
             "source": f"file:{path}"}
    return brief, payload["result"]


def run_job(brief, result, out_dir):
    """Run one job end to end (dry-run delivery) and return a summary.

    Writes report.md + verdict.json into out_dir. Delivery in D3 is
    files-only: nothing is submitted anywhere, no account is touched.
    """
    name, _jurisdiction = parse_brief(brief)
    verdict = verdict_for(name, result)
    markdown = render_markdown(name, result, verdict)
    os.makedirs(out_dir, exist_ok=True)
    report_path = os.path.join(out_dir, "report.md")
    verdict_path = os.path.join(out_dir, "verdict.json")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(markdown)
    with open(verdict_path, "w", encoding="utf-8") as f:
        f.write(render_json(verdict) + "\n")
    return {
        "job_source": brief.get("source", "unknown"),
        "name": name,
        "verdict": verdict["verdict"],
        "risk_score": verdict["risk_score"],
        "report_path": report_path,
        "verdict_path": verdict_path,
        "delivered": "dry-run files only — nothing submitted, no accounts touched",
    }


def _default_out_dir(scenario):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = scenario or "custom"
    return os.path.join(_REPO_ROOT, "runs", f"{label}-{stamp}")


def _refuse_live():
    """Live mode is not implemented in D3: say so and fail loudly."""
    missing = []
    if not os.environ.get("CLEARNAME_AGENT_API_TOKEN"):
        missing.append("CLEARNAME_AGENT_API_TOKEN env var (agent API token)")
    if not os.environ.get("CLEARNAME_MCP_ENDPOINT"):
        missing.append("CLEARNAME_MCP_ENDPOINT env var (MCP endpoint)")
    lines = ["live mode is not implemented in D3: the runner refuses to "
             "pretend to poll the market or submit a deliverable."]
    if missing:
        lines.append("also missing: " + "; ".join(missing))
    lines.append("live prerequisites (user browser steps, still pending): "
                 "(1) market listing switched to self-hosted, "
                 "(2) agent API token in env only, "
                 "(3) MCP handshake listing open jobs. "
                 "Until then, use --dry-run.")
    print("\n".join(lines), file=sys.stderr)
    return 2


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="ClearName self-hosted runtime v1 (D3)")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true",
                      help="run one fixture/custom job offline; files-only delivery")
    mode.add_argument("--live", action="store_true",
                      help="poll the live market (NOT implemented in D3; refuses)")
    ap.add_argument("--scenario", choices=list(SCENARIOS),
                    help="fixture assignment to run (dry-run only)")
    ap.add_argument("--result-json", default="",
                    help="custom engine result JSON {name, result} (dry-run only)")
    ap.add_argument("--out-dir", default="",
                    help="delivery directory (default runs/<scenario>-<UTC stamp>)")
    ap.add_argument("--guardrails-state", default="",
                    help="enforce guardrails with this JSON state file "
                    "(daily cap bookkeeping; default: no enforcement)")
    ap.add_argument("--price-usdc", type=float, default=None,
                    help="job price in USDC (live jobs only; fixture jobs omit it)")
    ap.add_argument("--min-price-usdc", type=float, default=None,
                    help="guardrail price band minimum (requires --guardrails-state)")
    ap.add_argument("--max-price-usdc", type=float, default=None,
                    help="guardrail price band maximum (requires --guardrails-state)")
    args = ap.parse_args(argv)

    if args.live:
        return _refuse_live()
    if args.result_json:
        brief, result = load_result_json(args.result_json)
        label = "custom"
    elif args.scenario:
        brief, result = load_scenario(args.scenario)
        label = args.scenario
    else:
        print("dry-run needs --scenario or --result-json", file=sys.stderr)
        return 2

    if args.guardrails_state:
        from agent.guardrails import (GuardrailViolation, Guardrails,
                                      validate_brief)
        try:
            validate_brief(brief["name"], brief.get("jurisdiction", "US"))
            guards = Guardrails(args.guardrails_state,
                                min_price_usdc=args.min_price_usdc,
                                max_price_usdc=args.max_price_usdc)
            guards.check_price(args.price_usdc)
            accepted = guards.check_and_record()
        except GuardrailViolation as exc:
            print(f"guardrail refused: {exc}", file=sys.stderr)
            return 2
        print(f"guardrails: brief ok, daily accepted count now {accepted}")

    out_dir = args.out_dir or _default_out_dir(label)
    try:
        summary = run_job(brief, result, out_dir)
    except (JobError, ValueError) as exc:
        print(f"job refused: {exc}", file=sys.stderr)
        return 2
    print(f"job received ({summary['job_source']})")
    print(f"brief parsed: name={summary['name']!r}, jurisdiction=US")
    print(f"verdict: {summary['verdict']} (risk score "
          f"{summary['risk_score']}/100)")
    print(f"report: {summary['report_path']}")
    print(f"verdict json: {summary['verdict_path']}")
    print(f"delivery: {summary['delivered']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
