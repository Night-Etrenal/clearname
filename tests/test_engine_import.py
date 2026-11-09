"""D1 fixture test: engine imports as a module and keeps its three-state discipline.

Offline only: all network access is stubbed, mirroring brand-checker/tests/.
Run from clearname-agent/:  python3 -m pytest tests/test_engine_import.py -q
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))

import brand_check as bc


def test_engine_module_surface():
    for name in ("check_domain", "check_trademarks", "render", "main", "suggest_variants"):
        assert callable(getattr(bc, name)), name


def test_domain_unknown_never_reported_available(monkeypatch):
    monkeypatch.setattr(bc, "http_get", lambda url, timeout=20, **kw: (404, "{}", url))
    monkeypatch.setattr(bc, "dns_resolves", lambda fqdn: None)
    res = bc.check_domain("zqxwvta", ".com")
    assert res["status"] == "unknown", res


def test_demo_samples_present():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for fname in ("zenvora-report.md", "nimbus-report.md"):
        path = os.path.join(base, "demo", "samples", fname)
        assert os.path.getsize(path) > 0, path
