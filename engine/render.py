"""ClearName delivery renderer (D2, NEAR HACKATHON, 2026-11-02).

Dual output for one checked name: a Markdown report for humans and the
frozen ``clearname-verdict-v1`` JSON for machines. Both are rendered
from the same engine result + verdict pair, so the two can never
disagree about the verdict.

This module performs no network I/O and never re-checks anything; it
only formats what the engine found and what ``verdict.py`` decided.
"""

import argparse
import json
import sys

try:
    from verdict import verdict_for
except ImportError:  # run as `python -m engine.render` from the repo root
    from engine.verdict import verdict_for


def render_json(verdict):
    """Serialize a verdict dict to indented JSON text."""
    return json.dumps(verdict, ensure_ascii=False, indent=2)


def _md_cell(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def render_markdown(name, result, verdict):
    """Render the human-readable Markdown report for one name."""
    dims = verdict["dimensions"]
    lines = []
    lines.append(f"# ClearName Verdict — {_md_cell(name)}")
    lines.append("")
    lines.append(f"- **Verdict: {verdict['verdict']}** (risk score "
                 f"{verdict['risk_score']}/100)")
    lines.append(f"- Checked at: {verdict['checked']}")
    lines.append(f"- Schema: {verdict['schema_version']}")
    lines.append("")
    lines.append("## Why this verdict")
    lines.append("")
    for reason in verdict["reasons"]:
        lines.append(f"- {reason}")
    lines.append("")

    lines.append("## Domains")
    lines.append("")
    lines.append(f"Status: {dims['domains']['status']} — "
                 f"{dims['domains']['summary']}")
    lines.append("")
    lines.append("| Domain | Status | Evidence | Checked at |")
    lines.append("|---|---|---|---|")
    for d in result["domains"]:
        lines.append(f"| {_md_cell(d['domain'])} | {d['status']} | "
                     f"{_md_cell(d['evidence'])} | {d['checked']} |")
    lines.append("")

    lines.append("## Social handles (exact handle)")
    lines.append("")
    lines.append(f"Status: {dims['socials']['status']} — "
                 f"{dims['socials']['summary']}")
    lines.append("")
    lines.append("| Platform | Handle | Status | Evidence | Checked at |")
    lines.append("|---|---|---|---|---|")
    for s in result["socials"]:
        lines.append(f"| {s['platform']} | [{s['handle']}]({s['url']}) | "
                     f"{s['status']} | {_md_cell(s['evidence'])} | {s['checked']} |")
    lines.append("")

    lines.append("## App stores")
    lines.append("")
    lines.append(f"Status: {dims['app_stores']['status']} — "
                 f"{dims['app_stores']['summary']}")
    lines.append("")
    itunes = result.get("itunes") or []
    lines.append(f"Apple App Store (source: {_md_cell(result.get('itunes_note') or '')}):")
    if itunes:
        for a in itunes:
            flag = " — **exact name match**" if a.get("exact") else ""
            lines.append(f"- [{_md_cell(a['name'])}]({a['url']}) by "
                         f"{_md_cell(a['developer'])}{flag}")
    else:
        lines.append("- No apps found for this name.")
    lines.append("")
    play = result.get("play") or []
    lines.append(f"Google Play (source: {_md_cell(result.get('play_note') or '')}):")
    if play:
        for a in play:
            lines.append(f"- [{_md_cell(a['package'])}]({a['url']})")
    else:
        lines.append("- No app listings extracted from the public search page.")
    lines.append("")

    lines.append("## Trademarks")
    lines.append("")
    lines.append(f"Status: {dims['trademarks']['status']} — "
                 f"{dims['trademarks']['summary']}")
    tm = result["trademarks"]
    lines.append(f"- Checked at: {tm['checked']}")
    lines.append(f"- Coverage: {tm['coverage']}")
    if tm.get("total") is not None:
        lines.append(f"- Total hits: {tm['total']}")
    else:
        lines.append("- Total hits: unknown.")
    if tm.get("marks"):
        lines.append("")
        lines.append("| Mark | Similarity | Serial No. | Registration No. | Status | Owner | Source |")
        lines.append("|---|---|---|---|---|---|---|")
        for m in tm["marks"]:
            lines.append(f"| {_md_cell(m['mark'])} | {m['similarity']} | "
                         f"{m['serial']} | {m['registration'] or '—'} | "
                         f"{_md_cell(m['status']) or 'see source'} | "
                         f"{_md_cell(m['owner'])} | [Justia record]({m['url']}) |")
    elif tm.get("available_source"):
        lines.append("- No identical or very similar marks found.")
    else:
        lines.append("- Trademark status: **couldn't confirm** (see coverage note).")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("Notes: Domain \"available\" means the registry RDAP had no "
                 "record and DNS did not resolve at query time; it is a "
                 "preliminary signal only, not a guarantee of registrability. "
                 "Trademark results are a starting point, not legal advice. "
                 "This tool never registers or purchases any checked name.")
    lines.append("")
    return "\n".join(lines)


def main(argv=None):
    """CLI: read an engine result JSON, print Markdown, optionally write JSON.

    Input JSON shape: {"name": str, "result": {...}} — one per-name
    engine result dict. Prints the Markdown report to stdout; with
    ``--json-out PATH`` also writes the verdict JSON there.
    """
    ap = argparse.ArgumentParser(description="ClearName delivery renderer (D2)")
    ap.add_argument("input", nargs="?",
                    help="JSON file with {name, result}; stdin if omitted")
    ap.add_argument("--json-out", default="",
                    help="also write the verdict JSON to this file")
    args = ap.parse_args(argv)
    if args.input:
        with open(args.input, encoding="utf-8") as f:
            payload = json.load(f)
    else:
        payload = json.load(sys.stdin)
    verdict = verdict_for(payload["name"], payload["result"])
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            f.write(render_json(verdict) + "\n")
    print(render_markdown(payload["name"], payload["result"], verdict))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
