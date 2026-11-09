"""Fixture tests for the ClearName verdict layer (D2).

Offline: every test builds its engine result dict by hand — no network,
no live lookups. Each test pins one verdict rule from ``verdict.py``'s
docstring, including the three-state discipline (unknown is never
counted as available).

Runs two ways (same assertions either way):

    python3 tests/test_verdict.py            # plain runner, no pytest needed
    python3 -m pytest tests/test_verdict.py -q
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engine"))

import verdict as verdict_mod
from verdict import verdict_for
from render import render_json, render_markdown

ITUNES_OK = "iTunes Search API, 0 result(s)"
PLAY_OK = "public Play search page (HTML scrape — weaker evidence)"
TM_COVERAGE = ("Database searched: USPTO federal trademark records via "
                "the Justia Trademarks mirror of USPTO data.")


def _dom(domain, status):
    return {"domain": domain, "status": status,
            "evidence": f"fixture evidence for {status}", "checked": "2026-11-02T00:00:00+08:00"}


def _soc(platform, status):
    return {"platform": platform, "handle": "testname", "status": status,
            "evidence": f"fixture evidence for {status}",
            "url": f"https://example.invalid/{platform}/testname",
            "checked": "2026-11-02T00:00:00+08:00"}


def _tm(marks=None, available=True):
    return {"available_source": available,
            "checked": "2026-11-02T00:00:00+08:00",
            "marks": marks or [],
            "total": len(marks) if marks else 0,
            "coverage": TM_COVERAGE}


def _mark(mark, similarity, status="PENDING"):
    return {"mark": mark, "similarity": similarity, "serial": "00000000",
            "registration": "", "status": status, "owner": "Fixture Owner",
            "url": "https://example.invalid/justia"}


def _base():
    return {
        "checked": "2026-11-02T00:00:00+08:00",
        "domains": [_dom("testname.com", "available"),
                    _dom("testname.io", "available"),
                    _dom("testname.ai", "available")],
        "socials": [_soc("x", "available"),
                    _soc("instagram", "available"),
                    _soc("tiktok", "available")],
        "itunes": [], "itunes_note": ITUNES_OK,
        "play": [], "play_note": PLAY_OK,
        "trademarks": _tm(),
    }


def test_all_clear():
    v = verdict_for("testname", _base())
    assert v["verdict"] == "CLEAR", v
    assert v["risk_score"] == 0, v
    assert v["schema_version"] == "clearname-verdict-v1"


def test_zenvora_blocked():
    res = _base()
    res["domains"] = [_dom("zenvora.com", "taken"),
                      _dom("zenvora.io", "taken"),
                      _dom("zenvora.ai", "taken")]
    res["socials"] = [_soc("x", "taken"),
                      _soc("instagram", "couldn't confirm"),
                      _soc("tiktok", "couldn't confirm")]
    res["trademarks"] = _tm([_mark("ZENVORA", "identical") for _ in range(4)])
    v = verdict_for("zenvora", res)
    assert v["verdict"] == "BLOCKED", v
    assert v["risk_score"] >= 40, v


def test_single_domain_taken_is_caution():
    res = _base()
    res["domains"][0] = _dom("testname.com", "taken")
    v = verdict_for("testname", res)
    assert v["verdict"] == "CAUTION", v
    assert v["risk_score"] == 20, v


def test_social_taken_only_is_caution():
    res = _base()
    res["socials"][0] = _soc("x", "taken")
    v = verdict_for("testname", res)
    assert v["verdict"] == "CAUTION", v
    assert v["risk_score"] == 15, v


def test_itunes_exact_match_is_caution():
    res = _base()
    res["itunes"] = [{"name": "Testname", "developer": "Someone",
                      "url": "https://example.invalid/app", "exact": True}]
    res["itunes_note"] = "iTunes Search API, 1 result(s)"
    v = verdict_for("testname", res)
    assert v["verdict"] == "CAUTION", v
    assert v["risk_score"] == 15, v


def test_unknown_domain_never_clears():
    # RDAP 404 + DNS inconclusive: the engine reports "unknown", and the
    # verdict layer must NOT count it as available.
    res = _base()
    res["domains"][0] = _dom("testname.com", "unknown")
    v = verdict_for("testname", res)
    assert v["verdict"] == "CAUTION", v
    assert v["risk_score"] == 10, v
    assert v["dimensions"]["domains"]["status"] == "unknown"


def test_trademark_source_unavailable_is_caution():
    res = _base()
    res["trademarks"] = _tm(available=False)
    v = verdict_for("testname", res)
    assert v["verdict"] == "CAUTION", v
    assert v["dimensions"]["trademarks"]["status"] == "unknown"


def test_all_unknown_is_caution_not_blocked():
    res = _base()
    res["domains"] = [_dom("testname.com", "unknown"),
                      _dom("testname.io", "unknown"),
                      _dom("testname.ai", "unknown")]
    res["socials"] = [_soc("x", "couldn't confirm"),
                      _soc("instagram", "couldn't confirm"),
                      _soc("tiktok", "couldn't confirm")]
    res["itunes_note"] = "iTunes Search API returned HTTP 500"
    res["play_note"] = "Google Play search page returned HTTP 503"
    res["trademarks"] = _tm(available=False)
    v = verdict_for("testname", res)
    assert v["verdict"] == "CAUTION", v  # no evidence -> never BLOCKED
    assert v["risk_score"] == 30, v  # 4 unknown dims, capped at 30


def test_very_similar_mark_blocks():
    res = _base()
    res["trademarks"] = _tm([_mark("TESTNAMEX", "very similar")])
    v = verdict_for("testname", res)
    assert v["verdict"] == "BLOCKED", v
    assert v["risk_score"] == 40, v


def test_soft_signals_do_not_change_verdict():
    res = _base()
    res["itunes"] = [{"name": "Testname Pro", "developer": "Someone",
                      "url": "https://example.invalid/app", "exact": False}]
    res["itunes_note"] = "iTunes Search API, 1 result(s)"
    res["play"] = [{"package": "com.example.testname",
                    "url": "https://example.invalid/play"}]
    v = verdict_for("testname", res)
    assert v["verdict"] == "CLEAR", v
    assert v["risk_score"] == 5, v
    assert any("soft signal" in r for r in v["reasons"])


def test_schema_shape():
    v = verdict_for("testname", _base())
    assert set(v) == {"schema_version", "name", "verdict", "risk_score",
                      "dimensions", "reasons", "checked"}
    assert set(v["dimensions"]) == {"domains", "socials", "app_stores",
                                   "trademarks"}
    assert v["verdict"] in ("CLEAR", "CAUTION", "BLOCKED")
    assert 0 <= v["risk_score"] <= 100


def test_malformed_input_raises():
    for bad_name, bad_result in (("", _base()),
                                 ("testname", {}),
                                 ("testname", {"domains": []})):
        try:
            verdict_for(bad_name, bad_result)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {bad_name!r}")


def test_render_agrees_with_verdict():
    res = _base()
    res["domains"] = [_dom("zenvora.com", "taken"),
                      _dom("zenvora.io", "taken"),
                      _dom("zenvora.ai", "taken")]
    res["trademarks"] = _tm([_mark("ZENVORA", "identical")])
    v = verdict_for("zenvora", res)
    md = render_markdown("zenvora", res, v)
    assert "Verdict: BLOCKED" in md
    assert "zenvora.com" in md and "ZENVORA" in md
    import json
    parsed = json.loads(render_json(v))
    assert parsed["verdict"] == "BLOCKED"
    assert parsed["schema_version"] == verdict_mod.SCHEMA_VERSION


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
