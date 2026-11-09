"""ClearName demo buyer v1 (D4, NEAR HACKATHON, 2026-11-02).

Simulates the founder-agent side of the story: hire ClearName to check
a candidate name, wait for delivery, then branch on the verdict:

- CLEAR  -> accept the delivery (demo only, no real payment released);
- BLOCKED -> do NOT accept; request variant suggestions instead
  (variant generation + re-check closing the loop is the D7 stretch;
  D4 records the request, it does not close the loop);
- CAUTION -> do NOT auto-accept; hold for human review.

Demo-only guarantees: this script never hires anything on the live
market, never pays, never touches an account. It drives the local
runtime (``agent/runner.py``) over fixture assignments, so the whole
hire -> deliver -> branch log is reproducible offline.

Usage (from the repo root):

    python -m buyer.demo_buyer --scenario zenvora
    python -m buyer.demo_buyer --scenario clear
    python -m buyer.demo_buyer --result-json path/to/result.json

Whether real self-hiring on the live market is allowed is a platform
question the plan flags for D4 verification (risk 6); nothing in this
script answers it, because nothing here reaches the live market.
"""

import argparse
import os
import sys
from datetime import datetime, timezone

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (_REPO_ROOT,):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from agent.runner import JobError, load_result_json, load_scenario, run_job  # noqa: E402

SCENARIOS = ("zenvora", "clear")


def _default_out_dir(label):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return os.path.join(_REPO_ROOT, "runs", f"buyer-{label}-{stamp}")


def hire_and_evaluate(brief, result, out_dir):
    """Run one simulated hire end to end; return (summary, action).

    Action is one of: "accept" (CLEAR), "request_variants" (BLOCKED),
    "hold_for_review" (CAUTION). All log lines are printed by the
    caller-facing ``main``; this function returns the facts.
    """
    summary = run_job(brief, result, out_dir)
    verdict = summary["verdict"]
    if verdict == "CLEAR":
        action = "accept"
    elif verdict == "BLOCKED":
        action = "request_variants"
    else:
        action = "hold_for_review"
    summary["action"] = action
    return summary


def _print_log(summary):
    print(f"buyer: 雇佣 ClearName 查名 {summary['name']!r} "
          f"(demo 模拟，未触碰真实市场、无付款)")
    print("buyer: 等待交付…")
    print(f"buyer: 收到交付，verdict={summary['verdict']} "
          f"(risk score {summary['risk_score']}/100)")
    print(f"buyer: 报告：{summary['report_path']}")
    print(f"buyer: 裁决 JSON：{summary['verdict_path']}")
    if summary["action"] == "accept":
        print("buyer: 裁决 CLEAR → 验收通过，交付完成 "
              "(demo 模拟，无真实放款)")
    elif summary["action"] == "request_variants":
        print("buyer: 裁决 BLOCKED → 不验收，请求变体建议 "
              "(变体生成与复查闭环为 D7 stretch，本 demo 只记录请求)")
    else:
        print("buyer: 裁决 CAUTION → 不自动验收，转人工复核")


def main(argv=None):
    ap = argparse.ArgumentParser(description="ClearName demo buyer v1 (D4)")
    ap.add_argument("--scenario", choices=list(SCENARIOS),
                    help="fixture assignment to hire for")
    ap.add_argument("--result-json", default="",
                    help="custom engine result JSON {name, result}")
    ap.add_argument("--out-dir", default="",
                    help="delivery directory (default runs/buyer-<label>-<UTC stamp>)")
    args = ap.parse_args(argv)

    if args.result_json:
        brief, result = load_result_json(args.result_json)
        label = "custom"
    elif args.scenario:
        brief, result = load_scenario(args.scenario)
        label = args.scenario
    else:
        print("demo hire needs --scenario or --result-json", file=sys.stderr)
        return 2

    out_dir = args.out_dir or _default_out_dir(label)
    try:
        summary = hire_and_evaluate(brief, result, out_dir)
    except (JobError, ValueError) as exc:
        print(f"hire refused: {exc}", file=sys.stderr)
        return 2
    _print_log(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
