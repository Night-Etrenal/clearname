#!/usr/bin/env python3
"""
brand_check.py — Brand Name Checker

Checks candidate brand names across four surfaces and emits a Markdown report:

  1. Domains      — RDAP (registry) lookup cross-checked with DNS resolution.
                    Results are three-state: available / taken / unknown.
                    A name is NEVER reported "available" on missing data.
  2. Social       — exact-handle probing on X, Instagram, TikTok public pages.
                    Results: taken / available / couldn't confirm. Platform
                    bot-walls and ambiguous pages yield "couldn't confirm",
                    which the report states explicitly (never guessed).
  3. App stores   — Apple App Store via the official iTunes Search API;
                    Google Play via its public search page (HTML scrape,
                    labelled as weaker evidence).
  4. Trademarks   — USPTO federal records. The official USPTO TM Search API
                    is attempted first; if unreachable, USPTO records are
                    queried through the Justia Trademarks mirror and the
                    report states exactly which database was searched and
                    what it covers.

Usage:
    python3 brand_check.py "Zenvora" "Nimbus"
    python3 brand_check.py "Zenvora" --tlds .com,.io,.ai \
        --platforms x,instagram,tiktok --tm-country US \
        --suggest --out report.md

The tool only READS public endpoints. It never registers, buys, or reserves
any name it checks.
"""

import argparse
import json
import re
import socket
import sys
import time
from datetime import datetime
from urllib.parse import quote, quote_plus
from zoneinfo import ZoneInfo

import requests

LOCAL_TZ = ZoneInfo("Asia/Shanghai")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
POLITE_DELAY = 0.4  # seconds between external calls

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA})


class RequestBudgetExceeded(Exception):
    """Raised when a single run exceeds its external-request budget."""


_CALL_BUDGET = 500  # max external HTTP calls per run (runaway guard)
_CALL_COUNT = 0


def _count_call():
    global _CALL_COUNT
    _CALL_COUNT += 1
    if _CALL_COUNT > _CALL_BUDGET:
        raise RequestBudgetExceeded(
            f"external request budget exceeded ({_CALL_BUDGET} calls/run)")


def md_cell(s):
    """Make a string safe for one Markdown table cell: no newlines, no raw
    pipe characters (both would break the table or inject rows)."""
    return str(s).replace("\r", " ").replace("\n", " ").replace("|", "\\|")

# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def now_iso():
    """Current time in local tz, ISO-8601 with offset."""
    return datetime.now(LOCAL_TZ).isoformat(timespec="seconds")


def http_get(url, timeout=20, **kw):
    """GET returning (status_code, text, final_url) or (None, error, url)."""
    _count_call()
    time.sleep(POLITE_DELAY)
    try:
        r = SESSION.get(url, timeout=timeout, allow_redirects=True, **kw)
        return r.status_code, r.text, r.url
    except requests.RequestException as e:
        return None, f"{type(e).__name__}: {e}", url


def http_post_json(url, payload, timeout=15):
    _count_call()
    time.sleep(POLITE_DELAY)
    try:
        r = SESSION.post(url, json=payload, timeout=timeout)
        return r.status_code, r.text, r.url
    except requests.RequestException as e:
        return None, f"{type(e).__name__}: {e}", url


def label_of(name):
    """Domain label / handle form of a brand name: lowercase alphanumerics."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


_dns_wildcard = None  # cached wildcard-resolver detection (None = untested)


def _dns_wildcard_detected():
    """True when the local resolver answers every name (captive/wildcard
    DNS, e.g. some sandboxes/VPNs). Such answers carry no information, so
    the DNS cross-check must be treated as inconclusive there."""
    global _dns_wildcard
    if _dns_wildcard is None:
        import secrets
        resolved = 0
        for _ in range(2):
            probe = f"wcmprobe{secrets.token_hex(8)}.com"
            try:
                socket.getaddrinfo(probe, None)
                resolved += 1
            except Exception:
                pass
        _dns_wildcard = (resolved == 2)
    return _dns_wildcard


def dns_resolves(fqdn):
    """True if the name resolves, False only on EAI_NONAME, None if unsure.
    Returns None for every query when the resolver is wildcard. A temporary
    failure (notably EAI_AGAIN) is inconclusive and must never collapse
    into 'does not resolve' — that collapse used to turn a transient
    outage into a false 'available' downstream."""
    if _dns_wildcard_detected():
        return None
    try:
        socket.getaddrinfo(fqdn, None)
        return True
    except socket.gaierror as exc:
        if exc.errno == socket.EAI_NONAME:
            return False
        return None
    except Exception:
        return None


def levenshtein(a, b):
    if abs(len(a) - len(b)) > 2:
        return 99
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


# ---------------------------------------------------------------------------
# 1. domains — RDAP + DNS
# ---------------------------------------------------------------------------

RDAP_EXTRA = {
    # rdap.org has no bootstrap entry for .io; the registry (Identity Digital)
    # runs its own RDAP endpoint, tried as a second source.
    ".io": "https://rdap.identity.digital/domain/{fqdn}",
}


def rdap_lookup(fqdn, tld):
    """
    Returns (verdict, evidence) where verdict in {"taken","not_found","no_service","error"}.
    """
    status, text, _ = http_get(f"https://rdap.org/domain/{fqdn}", timeout=20)
    if status == 200:
        registrar = ""
        try:
            data = json.loads(text)
            for ent in data.get("entities", []):
                if "registrar" in ent.get("roles", []):
                    for item in ent.get("vcardArray", [[], []])[1]:
                        if item and item[0] == "fn":
                            registrar = item[3]
            name = data.get("ldhName", fqdn)
        except Exception:
            name = fqdn
        ev = f"RDAP 200 via rdap.org (registered name: {name}"
        ev += f", registrar: {registrar}" if registrar else ""
        return "taken", ev + ")"
    if status == 404:
        if "No RDAP service" in text:
            pass  # fall through to registry endpoint below
        else:
            return "not_found", "RDAP 404 via rdap.org (registry has no record)"
    if tld in RDAP_EXTRA:
        url = RDAP_EXTRA[tld].format(fqdn=fqdn)
        status2, _text2, _ = http_get(url, timeout=15)
        if status2 == 200:
            return "taken", f"RDAP 200 via registry endpoint ({url.split('/domain')[0]})"
        if status2 == 404:
            return "not_found", "RDAP 404 via registry endpoint"
        return "error", (f"rdap.org: no RDAP service for {tld}; "
                         f"registry RDAP unreachable (HTTP {status2})")
    if status == 404:
        # Reached only when rdap.org answered 404 with "No RDAP service"
        # and no registry endpoint is configured for this TLD.
        return "error", (f"rdap.org has no RDAP service for {tld} and no "
                         f"registry endpoint is configured — registration "
                         f"unconfirmed")
    if status is None:
        return "error", f"RDAP request failed ({text})"
    return "error", f"RDAP returned HTTP {status} via rdap.org"


def check_domain(label, tld):
    fqdn = label + tld
    checked = now_iso()
    verdict, evidence = rdap_lookup(fqdn, tld)
    resolves = dns_resolves(fqdn)
    if verdict == "taken":
        status = "taken"
    elif verdict == "not_found":
        if resolves is True:
            status = "taken"
            evidence += "; DNS resolves despite RDAP 404 — treated as registered"
        elif resolves is False:
            status = "available"
            evidence += ("; DNS does not resolve (consistent) — preliminary "
                         "signal at query time only; premium/reserved names "
                         "may still be unregistrable")
        else:  # DNS check itself inconclusive (None) — never "available"
            status = "unknown"
            evidence += ("; DNS check inconclusive — registration cannot be "
                         "confirmed either way — NOT counted as available")
    else:  # RDAP gave no usable answer
        if resolves is True:
            status = "taken"
            evidence += "; DNS resolves — treated as registered"
        else:
            status = "unknown"
            evidence += ("; DNS does not resolve or DNS check inconclusive, "
                         "but registration cannot be confirmed either way — "
                         "NOT counted as available")
    return {"domain": fqdn, "status": status, "evidence": evidence, "checked": checked}


# ---------------------------------------------------------------------------
# 2. social handles — public page probing with explicit "couldn't confirm"
# ---------------------------------------------------------------------------

def check_x(handle):
    url = f"https://x.com/{handle}"
    status, text, _ = http_get(url, timeout=20)
    if status is None:
        return "couldn't confirm", f"request failed ({text})"
    if status == 429:
        return "couldn't confirm", "rate-limited (HTTP 429) — not counted as available"
    if status == 404:
        # A bare 404 from an unauthenticated probe is not proof of
        # availability: platforms serve generic 404s behind bot walls.
        return ("couldn't confirm",
                "HTTP 404 with no readable existence marker — not counted "
                "as available")
    low = text.lower()
    if "this account doesn't exist" in low or "this account doesn’t exist" in low:
        return "available", "profile page states the account doesn't exist"
    if f'"screen_name":"{handle.lower()}"' in low or f'"screenname":"{handle.lower()}"' in low:
        return "taken", "profile payload contains this screen_name"
    return ("couldn't confirm",
            f"HTTP {status}; X served its app shell without a readable "
            "existence marker (login/bot wall) — not guessed")


def check_instagram(handle):
    url = f"https://www.instagram.com/{handle}/"
    status, text, final = http_get(url, timeout=20)
    if status is None:
        return "couldn't confirm", f"request failed ({text})"
    if status == 429:
        return "couldn't confirm", "rate-limited (HTTP 429) — not counted as available"
    if status == 404:
        return ("couldn't confirm",
                "HTTP 404 with no readable existence marker — not counted "
                "as available")
    if "/accounts/login" in final:
        return "couldn't confirm", "redirected to login wall"
    if f'"username":"{handle.lower()}"' in text.lower():
        return "taken", "page payload contains this username"
    if "page isn't available" in text.lower() or "page isn’t available" in text.lower():
        return "available", "profile page states it isn't available"
    return ("couldn't confirm",
            f"HTTP {status}; Instagram served a login wall / no readable "
            "existence marker — not guessed")


def check_tiktok(handle):
    url = f"https://www.tiktok.com/@{handle}"
    status, text, _ = http_get(url, timeout=20)
    if status is None:
        return "couldn't confirm", f"request failed ({text})"
    if status == 429:
        return "couldn't confirm", "rate-limited (HTTP 429) — not counted as available"
    if status == 404:
        return ("couldn't confirm",
                "HTTP 404 with no readable existence marker — not counted "
                "as available")
    low = text.lower()
    if "couldn't find this account" in low or "couldn’t find this account" in low:
        return "available", "profile page states 'Couldn't find this account'"
    if f'"uniqueid":"{handle.lower()}"' in low:
        return "taken", "page payload contains this uniqueId"
    return ("couldn't confirm",
            f"HTTP {status}; no readable existence marker in page — not guessed")


SOCIAL_CHECKS = {"x": check_x, "instagram": check_instagram, "tiktok": check_tiktok}
SOCIAL_URL = {
    "x": "https://x.com/{h}",
    "instagram": "https://www.instagram.com/{h}/",
    "tiktok": "https://www.tiktok.com/@{h}",
}


def check_social(platform, handle):
    checked = now_iso()
    status, evidence = SOCIAL_CHECKS[platform](handle)
    return {"platform": platform, "handle": handle, "status": status,
            "evidence": evidence, "url": SOCIAL_URL[platform].format(h=handle),
            "checked": checked}


# ---------------------------------------------------------------------------
# 3. app stores
# ---------------------------------------------------------------------------

def check_itunes(name):
    url = ("https://itunes.apple.com/search?term=" + quote(name) +
           "&entity=software&limit=10&country=US")
    status, text, _ = http_get(url, timeout=20)
    if status != 200:
        return [], f"iTunes Search API returned HTTP {status}"
    try:
        data = json.loads(text)
    except Exception:
        return [], "iTunes Search API returned unparseable JSON"
    apps = []
    for r in data.get("results", []):
        apps.append({
            "name": r.get("trackName", ""),
            "developer": r.get("artistName", ""),
            "url": r.get("trackViewUrl", ""),
            "exact": r.get("trackName", "").strip().lower() == name.strip().lower(),
        })
    return apps, f"iTunes Search API, {data.get('resultCount', 0)} result(s)"


def check_google_play(name):
    url = ("https://play.google.com/store/search?q=" + quote_plus(name) +
           "&c=apps&gl=US&hl=en")
    status, text, _ = http_get(url, timeout=25)
    if status != 200 or status is None:
        return [], (f"Google Play search page returned HTTP {status} — "
                    "no official Play API exists; result unavailable")
    ids = []
    for m in re.finditer(r'href="/store/apps/details\?id=([\w\.]+)"', text):
        if m.group(1) not in ids:
            ids.append(m.group(1))
    apps = [{"package": pid,
             "url": f"https://play.google.com/store/apps/details?id={pid}"}
            for pid in ids[:10]]
    return apps, ("public Play search page (HTML scrape — weaker evidence; "
                  "package ids listed, titles not reliably extractable)")


# ---------------------------------------------------------------------------
# 4. trademarks — USPTO (official API attempted; Justia mirror of USPTO data
#    as the labelled fallback)
# ---------------------------------------------------------------------------

_uspto_official_state = {"tried": False, "ok": False, "note": ""}


def try_official_uspto():
    """One attempt at the official USPTO TM Search API per run."""
    if _uspto_official_state["tried"]:
        return _uspto_official_state["ok"]
    _uspto_official_state["tried"] = True
    status, text, _ = http_post_json(
        "https://tmsearch.uspto.gov/api-v1-0-0/tmsearch",
        {"query": {"bool": {"must": [{"query_string": {"query": "NIMBUS"}}]}},
         "size": 1},
        timeout=12)
    if status == 200 and "hits" in text:
        _uspto_official_state["ok"] = True
    else:
        _uspto_official_state["note"] = (
            f"official USPTO TM Search API unreachable from this network at "
            f"query time (HTTP {status})")
    return _uspto_official_state["ok"]


def justia_search(name):
    """Search USPTO records via the Justia Trademarks mirror. Returns
    (total, [marks], evidence_note). total is None when the results page
    could not be parsed (which is NOT the same as zero hits).
    Each mark: name/filed/owner/serial/url."""
    url = "https://trademarks.justia.com/search?q=" + quote_plus(name)
    status, text, _ = http_get(url, timeout=30)
    if status != 200 or status is None:
        return None, [], f"Justia Trademarks search returned HTTP {status}"
    m = re.search(r"Results\s+1\s*-\s*\d+\s*of\s*([\d,]+)", text)
    if m:
        total = int(m.group(1).replace(",", ""))
    elif "The requested page number is higher than the search results count" in text:
        # Justia's zero-hit behaviour: page 1 is requested but the result
        # count is 0, so it serves this error page instead of a "0 results"
        # page. Page 1 > count is only consistent with count == 0; the
        # inference is disclosed in the evidence note.
        total = 0
        return total, [], ("Justia Trademarks search page returned its "
                           "zero-results error page, interpreted as 0 hits")
    else:
        total = None
    links = {}
    for lm in re.finditer(r'href="((?:https://trademarks\.justia\.com)?/[^"]*?-(\d{8})\.html)"', text):
        href = lm.group(1)
        if href.startswith("/"):
            href = "https://trademarks.justia.com" + href
        links[lm.group(2)] = href
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = re.sub(r"\s+", " ", plain)
    marks = []
    pat = re.compile(
        r"([A-Z0-9][A-Z0-9 &'\-\.\,]{0,60}?)\s+Filed:\s+"
        r"([A-Z][a-z]+ \d{1,2}, \d{4})\s+(.*?)Owned by:\s*(.*?)\s*"
        r"Serial Number:\s*(\d{8})")
    for mm in pat.finditer(plain):
        serial = mm.group(5)
        marks.append({
            "mark": mm.group(1).strip(),
            "filed": mm.group(2),
            "owner": mm.group(4).strip(),
            "serial": serial,
            "url": links.get(serial, url),
            "registration": "",
            "status": "",
        })
    return total, marks, f"Justia Trademarks search page, {total} total hit(s)"


def justia_detail(mark):
    """Fetch a mark's detail page for registration number and status."""
    if not mark["url"] or "justia" not in mark["url"]:
        return
    url = mark["url"]
    if url.startswith("/"):
        url = "https://trademarks.justia.com" + url
    # Justia throttles bursty follow-up requests; slow down and retry once.
    status, text = None, ""
    for attempt in range(2):
        time.sleep(2.0 if attempt == 0 else 3.0)
        status, text, _ = http_get(url, timeout=20)
        if status == 200:
            break
    if status != 200:
        return
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = re.sub(r"\s+", " ", plain)
    rm = re.search(r"Registration Number\s*:?\s*(\d{6,8})", plain)
    if rm:
        mark["registration"] = rm.group(1)
    # Justia detail pages render e.g. "Status: 630 - New Application -
    # Record Initialized Not Assigned To Examiner Serial Number 99...".
    sm = re.search(r"Status:\s*([0-9]{3}\s*-\s*[^:]{3,80}?)\s+Serial Number", plain)
    if not sm:
        sm = re.search(r"Status:\s*([A-Za-z][A-Za-z /&\-]{2,50})", plain)
    if sm:
        mark["status"] = re.sub(r"\s+", " ", sm.group(1)).strip(" -")


def similarity(mark, query):
    a, b = mark.strip().lower(), query.strip().lower()
    if a == b:
        return "identical"
    if a.startswith(b) or b.startswith(a) or f" {b}" in f" {a}":
        return "very similar"
    if levenshtein(a, b) <= 2:
        return "very similar"
    return "other"


def check_trademarks(name, country="US"):
    checked = now_iso()
    try_official_uspto()
    if country != "US":
        return {"available_source": False, "checked": checked, "marks": [],
                "total": None,
                "coverage": (f"Only the US (USPTO) database is implemented in "
                             f"this tool; {country} was requested but NOT "
                             "searched — treat trademark status there as "
                             "couldn't confirm.")}
    total, marks, note = justia_search(name)
    if total is None and not marks:
        if note.startswith("Justia Trademarks search returned HTTP"):
            # Fetch failure (mirror unreachable).
            return {"available_source": False, "checked": checked, "marks": [],
                    "total": None,
                    "coverage": ("USPTO search could not be completed: " +
                                 _uspto_official_state["note"] +
                                 f"; mirror also failed ({note}). "
                                 "Trademark status: couldn't confirm.")}
        # Page fetched but its structure is unrecognized: this is a parse
        # failure, NOT evidence of zero conflicting marks.
        return {"available_source": False, "checked": checked, "marks": [],
                "total": None,
                "coverage": ("USPTO search could not be completed: the "
                             "results page structure was not recognized "
                             f"({note}). Trademark status: couldn't confirm "
                             "— a parse failure is not a clean result.")}
    if total is None and marks:
        # Marks were parsed but the total-hit count was not: keep the marks,
        # leave the total unknown rather than inventing a zero.
        note += "; total hit count could not be parsed from the page"
    if total is not None and total > 0 and not marks:
        # The mirror reports hits but zero rows parsed: the parse is
        # incomplete, NOT evidence of zero conflicting marks.
        return {"available_source": False, "checked": checked, "marks": [],
                "total": total,
                "coverage": ("USPTO search could not be completed: the "
                             "mirror reports hits but no result rows could "
                             "be parsed — a parse failure is not a clean "
                             "result. Trademark status: couldn't confirm.")}
    for mk in marks:
        mk["similarity"] = similarity(mk["mark"], name)
    relevant = [m for m in marks if m["similarity"] in ("identical", "very similar")]
    relevant.sort(key=lambda m: 0 if m["similarity"] == "identical" else 1)
    relevant = relevant[:10]
    # Enrich the top identical marks with registration number / status.
    for mk in [m for m in relevant if m["similarity"] == "identical"][:3]:
        justia_detail(mk)
    cov = ("Database searched: USPTO federal trademark records (registered "
           "marks and pending applications) via the Justia Trademarks "
           "mirror of USPTO data. RESULT ROWS COME FROM THE MIRROR ONLY: "
           "the official USPTO TM Search API is probed once per run for "
           "reachability but is NOT used to fetch result rows in this "
           "version")
    if _uspto_official_state["ok"]:
        cov += " (probe at query time: official API reachable)."
    else:
        cov += (" (probe at query time: " + _uspto_official_state["note"] + ").")
    cov += (" Coverage limits: the searched mirror page does not disclose "
            "its refresh time, so mirror lag vs. the official register is "
            "UNVERIFIED; the search is unfiltered by Nice class; US state "
            "registrations, common-law (unregistered) uses, and non-US "
            "registers are NOT covered. Similarity labels (identical / "
            "very similar) are string heuristics, not a trademark legal "
            "analysis.")
    cov += f" Search note: {note}."
    return {"available_source": True, "checked": checked, "marks": relevant,
            "total": total, "coverage": cov}


# ---------------------------------------------------------------------------
# variant suggestions
# ---------------------------------------------------------------------------

def suggest_variants(name, tlds, platforms):
    base = label_of(name)
    patterns = ["get{b}", "try{b}", "use{b}", "hey{b}",
                "{b}app", "{b}hq", "{b}labs", "{b}ly"]
    variants = []
    seen = set()
    for p in patterns:
        v = p.format(b=base)
        if v not in seen and v != base:
            seen.add(v)
            variants.append(v)
    results = []
    for v in variants:
        doms = [check_domain(v, t) for t in tlds]
        socials = [check_social(p, v) for p in platforms]
        # all() is True on empty lists: a variant checked against nothing
        # must never read as clear. Labels over 63 chars cannot be valid
        # DNS labels either.
        clear = ((bool(doms) or bool(socials)) and len(v) <= 63
                 and all(d["status"] == "available" for d in doms)
                 and all(s["status"] == "available" for s in socials))
        results.append({"variant": v, "domains": doms, "socials": socials,
                        "clear": clear})
    return results


# ---------------------------------------------------------------------------
# report rendering
# ---------------------------------------------------------------------------

def render(names, tlds, platforms, tm_country, all_results, suggestions):
    lines = []
    lines.append("# Brand Name Check Report")
    lines.append("")
    lines.append(f"- Generated: {now_iso()} (Asia/Shanghai) / "
                 f"{datetime.now(ZoneInfo('UTC')).isoformat(timespec='seconds')} (UTC)")
    lines.append(f"- Names checked: {', '.join(md_cell(n) for n in names)}")
    lines.append(f"- Domain suffixes: {', '.join(tlds)}")
    lines.append(f"- Social platforms: {', '.join(platforms)}")
    lines.append(f"- Trademark register: {tm_country} (USPTO)")
    lines.append("- Method: live queries to public RDAP/DNS, platform profile "
                 "pages, iTunes Search API, Google Play public search, and "
                 "USPTO records (see per-section coverage notes). "
                 "Three-state discipline: anything not positively confirmed "
                 "is reported as unknown / couldn't confirm, never as available.")
    lines.append("")
    for name, res in zip(names, all_results):
        lines.append(f"## {md_cell(name)}")
        lines.append("")
        lines.append("### Domains")
        lines.append("")
        lines.append("| Domain | Status | Evidence | Checked at |")
        lines.append("|---|---|---|---|")
        for d in res["domains"]:
            lines.append(f"| {md_cell(d['domain'])} | {d['status']} | {md_cell(d['evidence'])} | {d['checked']} |")
        lines.append("")
        lines.append("### Social handles (exact handle)")
        lines.append("")
        lines.append("| Platform | Handle | Status | Evidence | Checked at |")
        lines.append("|---|---|---|---|---|")
        for s in res["socials"]:
            lines.append(f"| {s['platform']} | [{s['handle']}]({s['url']}) | "
                         f"{s['status']} | {md_cell(s['evidence'])} | {s['checked']} |")
        lines.append("")
        lines.append("### App stores")
        lines.append("")
        lines.append(f"**Apple App Store** (source: {md_cell(res['itunes_note'])}; checked {res['checked']}):")
        if res["itunes"]:
            for a in res["itunes"]:
                flag = " — **exact name match**" if a["exact"] else ""
                lines.append(f"- [{md_cell(a['name'])}]({a['url']}) by {md_cell(a['developer'])}{flag}")
        else:
            lines.append("- No apps found for this name.")
        lines.append("")
        lines.append(f"**Google Play** (source: {md_cell(res['play_note'])}; checked {res['checked']}):")
        if res["play"]:
            for a in res["play"]:
                lines.append(f"- [{md_cell(a['package'])}]({a['url']})")
        else:
            lines.append("- No app listings extracted from the public search page.")
        lines.append("")
        lines.append("### Trademarks")
        lines.append("")
        tm = res["trademarks"]
        lines.append(f"- Checked at: {tm['checked']}")
        lines.append(f"- Coverage: {tm['coverage']}")
        if tm["total"] is not None:
            lines.append(f"- Total hits for the search term in the searched "
                         f"database: {tm['total']}")
        else:
            lines.append("- Total hits: unknown (count could not be parsed "
                         "from the results page; see coverage note).")
        if tm["marks"]:
            lines.append("")
            lines.append("| Mark | Similarity | Serial No. | Registration No. | Status | Owner | Source |")
            lines.append("|---|---|---|---|---|---|---|")
            for m in tm["marks"]:
                lines.append(f"| {md_cell(m['mark'])} | {m['similarity']} | {m['serial']} | "
                             f"{m['registration'] or '—'} | {md_cell(m['status']) or 'see source'} | "
                             f"{md_cell(m['owner'])} | [Justia record]({m['url']}) |")
        elif tm["available_source"]:
            lines.append("- No identical or very similar marks found in the "
                         "searched database (first results page reviewed in full).")
        else:
            lines.append("- Trademark status: **couldn't confirm** (see coverage note).")
        lines.append("")
    if suggestions is not None:
        lines.append("## Suggested variants")
        lines.append("")
        lines.append("Variants are recommended ONLY if, at query time, every "
                     "checked domain suffix was \"available\" AND every checked "
                     "platform handle was positively \"available\" (a single "
                     "\"couldn't confirm\" disqualifies the variant).")
        lines.append("")
        clear = [s for s in suggestions if s["clear"]]
        if clear:
            for s in clear:
                doms = ", ".join(f"{d['domain']}: {d['status']}" for d in s["domains"])
                socs = ", ".join(f"{x['platform']}: {x['status']}" for x in s["socials"])
                lines.append(f"- **{s['variant']}** — clear on checked domains and socials ({doms}; {socs})")
        else:
            lines.append("No variant was clear on checked domains and socials at query time.")
            for s in suggestions:
                doms = ", ".join(f"{d['domain']}: {d['status']}" for d in s["domains"])
                socs = ", ".join(f"{x['platform']}: {x['status']}" for x in s["socials"])
                lines.append(f"- {s['variant']} — not clear on checked domains and socials ({doms}; {socs})")
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("Notes: Domain \"available\" means the registry RDAP had no "
                 "record and DNS did not resolve at query time; it is a "
                 "preliminary signal only, not a guarantee of registrability "
                 "(premium/reserved names exist). "
                 "This tool never registers or purchases any checked name.")
    lines.append("")
    lines.append("trademark results are a starting point, not legal advice")
    lines.append("")
    lines.append("Privacy: query terms are sent to the public sources named "
                 "above as part of each lookup; this tool stores nothing "
                 "except the report file you choose to write with --out. "
                 "Do not use it to probe names you must keep confidential.")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description="Brand name availability checker")
    ap.add_argument("names", nargs="+", help="candidate brand names")
    ap.add_argument("--tlds", default=".com,.io,.ai",
                    help="comma-separated domain suffixes (default .com,.io,.ai)")
    ap.add_argument("--platforms", default="x,instagram,tiktok",
                    help="comma-separated platforms: x,instagram,tiktok")
    ap.add_argument("--tm-country", default="US",
                    help="trademark register (only US/USPTO implemented)")
    ap.add_argument("--suggest", action="store_true",
                    help="also check name variants and list fully-clear ones")
    ap.add_argument("--out", default="", help="write report to this file too")
    args = ap.parse_args(argv)

    tlds = [t.strip() if t.strip().startswith(".") else "." + t.strip()
            for t in args.tlds.split(",") if t.strip()]
    platforms = [p.strip().lower() for p in args.platforms.split(",") if p.strip()]
    for p in platforms:
        if p not in SOCIAL_CHECKS:
            print(f"error: unknown platform '{p}'", file=sys.stderr)
            return 2

    # ---- input validation (task book ch. 3D) ----
    for name in args.names:
        stripped = name.strip()
        if not stripped:
            print("error: empty brand name", file=sys.stderr)
            return 2
        if len(name) > 100:
            print(f"error: brand name too long ({len(name)} chars, max 100)",
                  file=sys.stderr)
            return 2
        if any(ord(c) < 32 or ord(c) == 127 for c in name):
            print("error: brand name contains control characters",
                  file=sys.stderr)
            return 2
        label = label_of(name)
        if not label:
            print(f"error: brand name {name!r} has no usable label "
                  f"(letters/digits only after normalization)",
                  file=sys.stderr)
            return 2
        if len(label) > 63:
            print(f"error: brand label too long for DNS ({len(label)} chars, "
                  f"max 63)", file=sys.stderr)
            return 2
    for t in tlds:
        if not re.fullmatch(r"\.[a-z]{2,24}", t):
            print(f"error: invalid domain suffix {t!r} (expected .xxx)",
                  file=sys.stderr)
            return 2

    all_results = []
    try:
        for name in args.names:
            label = label_of(name)
            res = {"checked": now_iso()}
            res["domains"] = [check_domain(label, t) for t in tlds]
            res["socials"] = [check_social(p, label) for p in platforms]
            res["itunes"], res["itunes_note"] = check_itunes(name)
            res["play"], res["play_note"] = check_google_play(name)
            res["trademarks"] = check_trademarks(name, args.tm_country)
            all_results.append(res)
    except RequestBudgetExceeded as e:
        print(f"error: {e}", file=sys.stderr)
        return 3

    suggestions = None
    if args.suggest and len(args.names) == 1:
        try:
            suggestions = suggest_variants(args.names[0], tlds, platforms)
        except RequestBudgetExceeded as e:
            print(f"error: {e}", file=sys.stderr)
            return 3

    report = render(args.names, tlds, platforms, args.tm_country,
                    all_results, suggestions)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
