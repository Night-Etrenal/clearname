"""ClearName verdict layer (D2, NEAR HACKATHON, 2026-11-02).

Consumes one per-name result dict exactly as produced by
``engine/brand_check.py`` (``main`` loop) and aggregates the four
dimensions — domains, social handles, app stores, trademarks — into a
machine-readable verdict plus a risk score.

Three-state discipline (inherited from the engine, never relaxed here):
anything not positively confirmed is ``unknown`` / ``couldn't confirm``,
never ``available``. An unknown dimension can push a verdict to CAUTION
but can never by itself produce BLOCKED, and a name with any unknown
dimension can never be CLEAR.

Verdict rules (applied in order, documented so a reviewer can re-derive
every verdict by hand):

1. BLOCKED if trademarks conflict (any identical or very-similar mark
   found in the searched register) OR every checked domain is taken.
2. Otherwise CAUTION if any dimension conflicts (a taken domain, a
   taken social handle, an exact-name app) OR any dimension is unknown.
3. Otherwise CLEAR.

Soft signals (non-exact iTunes matches, Google Play package listings
whose titles the engine cannot reliably extract) are disclosed in the
dimension notes and add a small risk-score contribution, but do NOT
change the verdict on their own: the engine's Play evidence is an HTML
scrape and cannot prove a same-name app exists.

Risk score (0-100, informational; the verdict is rule-based, not
threshold-based):

- trademark conflict: +40
- all checked domains taken: +30
- any domain taken (not all): +20
- any social handle taken: +15
- exact-name app on iTunes: +15
- each unknown dimension: +10 (capped at +30 total)
- soft signals (non-exact iTunes matches / Play listings): +5

Frozen JSON schema (``clearname-verdict-v1``)::

    {
      "schema_version": "clearname-verdict-v1",
      "name": str,
      "verdict": "CLEAR" | "CAUTION" | "BLOCKED",
      "risk_score": int,  # 0-100
      "dimensions": {
        "domains":    {"status": "clear|conflict|unknown", "summary": str, "items": [...]},
        "socials":    {"status": "clear|conflict|unknown", "summary": str, "items": [...]},
        "app_stores": {"status": "clear|conflict|unknown", "summary": str, "items": {...}},
        "trademarks": {"status": "clear|conflict|unknown", "summary": str, "items": {...}}
      },
      "reasons": [str],
      "checked": str  # ISO timestamp, copied from the engine result
    }

This module never performs network I/O and never mutates the engine's
checking logic; it only aggregates results the engine already produced.
"""

import argparse
import json
import sys

SCHEMA_VERSION = "clearname-verdict-v1"

VERDICT_SCHEMA = {
    "schema_version": SCHEMA_VERSION,
    "name": "candidate brand name, as checked",
    "verdict": "CLEAR | CAUTION | BLOCKED",
    "risk_score": "integer 0-100, informational; verdict is rule-based",
    "dimensions": {
        "domains": "status clear|conflict|unknown; items = engine domain rows",
        "socials": "status clear|conflict|unknown; items = engine social rows",
        "app_stores": "status clear|conflict|unknown; items = {itunes, play, notes}",
        "trademarks": "status clear|conflict|unknown; items = engine trademark block",
    },
    "reasons": "human-readable lines explaining the verdict, in order",
    "checked": "ISO timestamp copied from the engine result",
}

# Score weights (see module docstring).
_W_TRADEMARK_CONFLICT = 40
_W_ALL_DOMAINS_TAKEN = 30
_W_ANY_DOMAIN_TAKEN = 20
_W_SOCIAL_TAKEN = 15
_W_APP_EXACT = 15
_W_UNKNOWN_DIMENSION = 10
_W_UNKNOWN_CAP = 30
_W_SOFT_SIGNAL = 5


def _assess_domains(domains):
    """Return (status, summary, score, reasons) for the domain dimension."""
    if not domains:
        return ("unknown", "no domains were checked — cannot clear on zero evidence",
                _W_UNKNOWN_DIMENSION, ["no domain suffix was checked"])
    taken = [d["domain"] for d in domains if d.get("status") == "taken"]
    unknown = [d["domain"] for d in domains
               if d.get("status") not in ("taken", "available")]
    if taken and len(taken) == len(domains):
        return ("conflict",
                "every checked domain is taken: " + ", ".join(taken),
                _W_ALL_DOMAINS_TAKEN,
                ["all checked domains taken: " + ", ".join(taken)])
    if taken:
        return ("conflict",
                "taken: " + ", ".join(taken),
                _W_ANY_DOMAIN_TAKEN,
                ["domain taken: " + ", ".join(taken)])
    if unknown:
        return ("unknown",
                "unconfirmed: " + ", ".join(unknown) +
                " (unknown is never counted as available)",
                _W_UNKNOWN_DIMENSION,
                ["domain status unknown: " + ", ".join(unknown)])
    return ("clear", "all checked domains available", 0, [])


def _assess_socials(socials):
    """Return (status, summary, score, reasons) for the social dimension."""
    if not socials:
        return ("unknown", "no social platforms were checked — cannot clear on zero evidence",
                _W_UNKNOWN_DIMENSION, ["no social platform was checked"])
    taken = [s["platform"] for s in socials if s.get("status") == "taken"]
    unknown = [s["platform"] for s in socials
               if s.get("status") not in ("taken", "available")]
    if taken:
        return ("conflict",
                "handle taken on: " + ", ".join(taken),
                _W_SOCIAL_TAKEN,
                ["social handle taken on: " + ", ".join(taken)])
    if unknown:
        return ("unknown",
                "couldn't confirm on: " + ", ".join(unknown) +
                " (login/bot walls are never counted as available)",
                _W_UNKNOWN_DIMENSION,
                ["social handle couldn't confirm on: " + ", ".join(unknown)])
    return ("clear", "all checked handles available", 0, [])


def _source_failed(note):
    """True when an engine source note reports a fetch/parse failure."""
    note = note or ""
    return "returned HTTP" in note or "unparseable" in note


def _assess_app_stores(itunes, itunes_note, play, play_note):
    """Return (status, summary, score, reasons) for the app-store dimension.

    Conflict requires an exact-name iTunes match. A failed source fetch
    makes the dimension unknown. Non-exact iTunes matches and Play
    listings are soft signals only (see module docstring).
    """
    if _source_failed(itunes_note) or _source_failed(play_note):
        failed = []
        if _source_failed(itunes_note):
            failed.append("Apple App Store")
        if _source_failed(play_note):
            failed.append("Google Play")
        return ("unknown",
                "source unavailable: " + ", ".join(failed),
                _W_UNKNOWN_DIMENSION,
                ["app-store source unavailable: " + ", ".join(failed)])
    if not itunes and not play and not itunes_note and not play_note:
        # No evidence at all: neither a result list nor a note. "No apps
        # found" would claim a completed search that never happened.
        return ("unknown",
                "no app-store evidence supplied — status couldn't confirm",
                _W_UNKNOWN_DIMENSION,
                ["app-store evidence missing (no lists, no notes)"])
    exact = [a["name"] for a in (itunes or []) if a.get("exact")]
    if exact:
        return ("conflict",
                "exact-name app on Apple App Store: " + ", ".join(exact),
                _W_APP_EXACT,
                ["exact-name app on Apple App Store: " + ", ".join(exact)])
    soft = []
    nonexact = [a["name"] for a in (itunes or []) if not a.get("exact")]
    if nonexact:
        soft.append(f"{len(nonexact)} similarly named app(s) on Apple App Store (non-exact)")
    if play:
        soft.append(f"{len(play)} Play listing(s) found (titles not reliably extractable — soft signal only)")
    if soft:
        return ("clear",
                "no exact-name app; soft signals noted: " + "; ".join(soft),
                _W_SOFT_SIGNAL,
                ["soft signal (not decisive): " + "; ".join(soft)])
    return ("clear", "no apps found for this name", 0, [])


def _assess_trademarks(tm):
    """Return (status, summary, score, reasons) for the trademark dimension."""
    if not tm or not tm.get("available_source"):
        return ("unknown",
                "trademark search could not be completed — status couldn't confirm",
                _W_UNKNOWN_DIMENSION,
                ["trademark search incomplete (source unavailable or unparsed)"])
    marks = tm.get("marks") or []
    if marks:
        identical = [m["mark"] for m in marks if m.get("similarity") == "identical"]
        similar = [m["mark"] for m in marks if m.get("similarity") == "very similar"]
        desc = []
        if identical:
            desc.append("identical: " + ", ".join(identical))
        if similar:
            desc.append("very similar: " + ", ".join(similar))
        return ("conflict",
                "conflicting mark(s) in USPTO register (Justia mirror): " + "; ".join(desc),
                _W_TRADEMARK_CONFLICT,
                ["trademark conflict: " + "; ".join(desc)])
    return ("clear",
            "no identical or very similar marks found in the searched register",
            0, [])


def verdict_for(name, result):
    """Aggregate one engine result dict into a ``clearname-verdict-v1`` dict.

    ``result`` must be one per-name dict from ``brand_check.main``'s loop
    (keys: checked, domains, socials, itunes, itunes_note, play,
    play_note, trademarks). Raises ValueError on malformed input rather
    than guessing a verdict.
    """
    if not name or not isinstance(name, str):
        raise ValueError("name must be a non-empty string")
    if not isinstance(result, dict):
        raise ValueError("result must be an engine per-name result dict")
    for key in ("domains", "socials", "trademarks"):
        if key not in result:
            raise ValueError(f"result is missing required key: {key}")

    d_status, d_summary, d_score, d_reasons = _assess_domains(result["domains"])
    s_status, s_summary, s_score, s_reasons = _assess_socials(result["socials"])
    if all(k in result for k in
           ("itunes", "itunes_note", "play", "play_note")):
        a_status, a_summary, a_score, a_reasons = _assess_app_stores(
            result.get("itunes") or [], result.get("itunes_note") or "",
            result.get("play") or [], result.get("play_note") or "")
    else:
        # App-store evidence missing entirely: unknown, never "no apps
        # found". A missing key must not silently become an empty list.
        a_status, a_summary, a_score, a_reasons = (
            "unknown", "app-store data missing — status couldn't confirm",
            _W_UNKNOWN_DIMENSION, ["app-store data missing from result"])
    t_status, t_summary, t_score, t_reasons = _assess_trademarks(result["trademarks"])

    unknown_count = sum(1 for st in (d_status, s_status, a_status, t_status)
                        if st == "unknown")
    unknown_score = min(unknown_count * _W_UNKNOWN_DIMENSION, _W_UNKNOWN_CAP)
    # Unknown points are counted once via the capped term, not per
    # dimension on top of the dimension scores, so recompute: dimension
    # scores above already include one unknown term each; replace them.
    score = d_score + s_score + a_score + t_score
    if unknown_count:
        per_dim_unknown = unknown_count * _W_UNKNOWN_DIMENSION
        score = score - per_dim_unknown + unknown_score
    score = max(0, min(100, score))

    dims = {
        "domains": {"status": d_status, "summary": d_summary,
                    "items": result["domains"]},
        "socials": {"status": s_status, "summary": s_summary,
                    "items": result["socials"]},
        "app_stores": {"status": a_status, "summary": a_summary,
                       "items": {"itunes": result.get("itunes") or [],
                                 "itunes_note": result.get("itunes_note") or "",
                                 "play": result.get("play") or [],
                                 "play_note": result.get("play_note") or ""}},
        "trademarks": {"status": t_status, "summary": t_summary,
                       "items": result["trademarks"]},
    }

    reasons = d_reasons + s_reasons + a_reasons + t_reasons
    if t_status == "conflict" or (d_status == "conflict"
                                  and len(result["domains"]) > 0
                                  and all(d.get("status") == "taken"
                                          for d in result["domains"])):
        verdict = "BLOCKED"
        reasons.append("verdict BLOCKED: trademark conflict or every domain taken")
    elif ("conflict" in (d_status, s_status, a_status, t_status)
          or "unknown" in (d_status, s_status, a_status, t_status)):
        verdict = "CAUTION"
        reasons.append("verdict CAUTION: a conflict or an unconfirmed dimension exists")
    else:
        verdict = "CLEAR"
        reasons.append("verdict CLEAR: all four dimensions clear")

    return {
        "schema_version": SCHEMA_VERSION,
        "name": name,
        "verdict": verdict,
        "risk_score": score,
        "dimensions": dims,
        "reasons": reasons,
        "checked": result.get("checked") or "",
    }


def main(argv=None):
    """CLI: read an engine result JSON, print the verdict JSON.

    Input JSON shape: {"name": str, "result": {...}} — one per-name
    engine result dict. Reads from the file given, or stdin when omitted.
    """
    ap = argparse.ArgumentParser(description="ClearName verdict layer (D2)")
    ap.add_argument("input", nargs="?",
                    help="JSON file with {name, result}; stdin if omitted")
    args = ap.parse_args(argv)
    if args.input:
        with open(args.input, encoding="utf-8") as f:
            payload = json.load(f)
    else:
        payload = json.load(sys.stdin)
    verdict = verdict_for(payload["name"], payload["result"])
    print(json.dumps(verdict, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
