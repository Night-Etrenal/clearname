"""Regression tests for the 2026-11-02 Terra audit fixes (verified items).

Offline: every network call is stubbed. Covers the five confirmed items:
DNS transient failure, trademark total-without-rows, app-store missing
or empty evidence, variant empty-set clear, and non-finite prices.

Runs two ways (same assertions either way):

    python3 tests/test_audit_fixes.py
    python3 -m pytest tests/test_audit_fixes.py -q
"""

import os
import socket
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "engine"))
sys.path.insert(0, os.path.join(REPO_ROOT, "agent"))

import brand_check as bc
from guardrails import GuardrailViolation, Guardrails
from verdict import verdict_for


def _base_result():
    return {
        "checked": "2026-11-02T00:00:00+08:00",
        "domains": [{"domain": "testname.com", "status": "available",
                     "evidence": "fixture", "checked": "2026-11-02T00:00:00+08:00"}],
        "socials": [{"platform": "x", "handle": "testname",
                     "status": "available", "evidence": "fixture",
                     "url": "https://example.invalid/x/testname",
                     "checked": "2026-11-02T00:00:00+08:00"}],
        "trademarks": {"available_source": True,
                       "checked": "2026-11-02T00:00:00+08:00",
                       "marks": [], "total": 0, "coverage": "fixture"},
    }


def test_dns_transient_failure_is_unknown():
    orig_wildcard = bc._dns_wildcard
    orig_getaddrinfo = socket.getaddrinfo
    try:
        bc._dns_wildcard = False

        def _again(fqdn, *args, **kwargs):
            raise socket.gaierror(socket.EAI_AGAIN, "Temporary failure")

        socket.getaddrinfo = _again
        assert bc.dns_resolves("example.com") is None

        def _noname(fqdn, *args, **kwargs):
            raise socket.gaierror(socket.EAI_NONAME, "Name not known")

        socket.getaddrinfo = _noname
        assert bc.dns_resolves("example.com") is False
    finally:
        bc._dns_wildcard = orig_wildcard
        socket.getaddrinfo = orig_getaddrinfo


def test_dns_transient_plus_rdap_404_is_unknown():
    orig_wildcard = bc._dns_wildcard
    orig_getaddrinfo = socket.getaddrinfo
    orig_http_get = bc.http_get
    try:
        bc._dns_wildcard = False

        def _again(fqdn, *args, **kwargs):
            raise socket.gaierror(socket.EAI_AGAIN, "Temporary failure")

        socket.getaddrinfo = _again
        bc.http_get = lambda url, timeout=20, **kw: (404, "{}", url)
        res = bc.check_domain("zqxwvta", ".com")
        assert res["status"] == "unknown", res
    finally:
        bc._dns_wildcard = orig_wildcard
        socket.getaddrinfo = orig_getaddrinfo
        bc.http_get = orig_http_get


def test_trademark_total_without_rows_is_unknown():
    orig_search = bc.justia_search
    orig_official = bc.try_official_uspto
    try:
        bc.try_official_uspto = lambda: None
        bc.justia_search = lambda name: (5, [], "Results 1 - 0 of 5")
        res = bc.check_trademarks("zenvora")
        assert res["available_source"] is False, res
        assert res["marks"] == []
    finally:
        bc.justia_search = orig_search
        bc.try_official_uspto = orig_official


def test_trademark_genuine_zero_stays_available():
    orig_search = bc.justia_search
    orig_official = bc.try_official_uspto
    try:
        bc.try_official_uspto = lambda: None
        bc.justia_search = lambda name: (0, [], "Results 1 - 0 of 0")
        res = bc.check_trademarks("zenvora")
        assert res["available_source"] is True, res
    finally:
        bc.justia_search = orig_search
        bc.try_official_uspto = orig_official


def test_trademark_parsed_rows_without_conflict_stays_available():
    orig_search = bc.justia_search
    orig_official = bc.try_official_uspto
    orig_detail = bc.justia_detail
    try:
        bc.try_official_uspto = lambda: None
        bc.justia_detail = lambda mk: None
        bc.justia_search = lambda name: (
            5, [{"mark": "otherbrand", "filed": "", "owner": "",
                 "serial": "", "url": ""}], "ok")
        res = bc.check_trademarks("zenvora")
        assert res["available_source"] is True, res
    finally:
        bc.justia_search = orig_search
        bc.try_official_uspto = orig_official
        bc.justia_detail = orig_detail


def test_variant_empty_sets_not_clear():
    res = bc.suggest_variants("zenvora", [], [])
    assert res, "expected variant suggestions"
    assert all(not r["clear"] for r in res), res


def test_variant_checked_available_stays_clear():
    orig_dom = bc.check_domain
    orig_soc = bc.check_social
    try:
        bc.check_domain = lambda v, t: {"domain": v + t, "status": "available"}
        bc.check_social = lambda p, v: {"platform": p, "status": "available"}
        res = bc.suggest_variants("zenvora", [".com"], ["x"])
        assert all(r["clear"] for r in res), res
    finally:
        bc.check_domain = orig_dom
        bc.check_social = orig_soc


def test_variant_overlong_label_not_clear():
    orig_dom = bc.check_domain
    orig_soc = bc.check_social
    try:
        bc.check_domain = lambda v, t: {"domain": v + t, "status": "available"}
        bc.check_social = lambda p, v: {"platform": p, "status": "available"}
        res = bc.suggest_variants("a" * 63, [".com"], ["x"])
        assert all(not r["clear"] for r in res), res
    finally:
        bc.check_domain = orig_dom
        bc.check_social = orig_soc


def test_app_store_missing_keys_is_unknown():
    v = verdict_for("testname", _base_result())
    assert v["dimensions"]["app_stores"]["status"] == "unknown", v
    assert v["verdict"] != "CLEAR", v


def test_app_store_empty_evidence_is_unknown():
    res = _base_result()
    res.update({"itunes": [], "itunes_note": "", "play": [], "play_note": ""})
    v = verdict_for("testname", res)
    assert v["dimensions"]["app_stores"]["status"] == "unknown", v
    assert v["verdict"] != "CLEAR", v


def test_app_store_mixed_presence_is_unknown():
    res = _base_result()
    res.update({"itunes": [], "itunes_note": "iTunes Search API, 0 result(s)"})
    v = verdict_for("testname", res)
    assert v["dimensions"]["app_stores"]["status"] == "unknown", v
    assert v["verdict"] != "CLEAR", v


def test_app_store_present_note_stays_clear():
    res = _base_result()
    res.update({"itunes": [], "itunes_note": "iTunes Search API, 0 result(s)",
                "play": [], "play_note": "public Play search page"})
    v = verdict_for("testname", res)
    assert v["dimensions"]["app_stores"]["status"] == "clear", v


def test_price_nonfinite_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        g = Guardrails(os.path.join(tmp, "state.json"),
                       min_price_usdc=1, max_price_usdc=10)
        for bad in (float("nan"), float("inf"), float("-inf"), "5", True):
            try:
                g.check_price(bad)
            except GuardrailViolation:
                pass
            else:
                raise AssertionError(
                    f"expected GuardrailViolation for {bad!r}")
        g.check_price(5)
        g.check_price(None)


def test_price_huge_int_rejected_not_crash():
    # math.isfinite raises OverflowError on gigantic ints; a guardrail
    # must refuse with GuardrailViolation, never crash.
    with tempfile.TemporaryDirectory() as tmp:
        g = Guardrails(os.path.join(tmp, "state.json"),
                       min_price_usdc=1, max_price_usdc=10)
        try:
            g.check_price(10 ** 400)
        except GuardrailViolation:
            pass
        else:
            raise AssertionError("expected GuardrailViolation for 10**400")
        try:
            Guardrails(os.path.join(tmp, "state2.json"),
                       min_price_usdc=10 ** 400)
        except GuardrailViolation:
            pass
        else:
            raise AssertionError(
                "expected GuardrailViolation for huge bound")


def test_price_band_validation():
    with tempfile.TemporaryDirectory() as tmp:
        for kwargs in ({"min_price_usdc": 10, "max_price_usdc": 1},
                       {"min_price_usdc": -1},
                       {"min_price_usdc": float("inf")},
                       {"max_price_usdc": float("nan")}):
            try:
                Guardrails(os.path.join(tmp, "state.json"), **kwargs)
            except GuardrailViolation:
                pass
            else:
                raise AssertionError(
                    f"expected GuardrailViolation for {kwargs}")


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
