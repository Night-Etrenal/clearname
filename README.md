# ClearName

An autonomous due-diligence agent for the NEAR AI Agent Marketplace. Other agents hire ClearName **before spending money on a brand name**: it checks the name across domains, social handles, app stores and US trademarks, then returns a machine-readable **CLEAR / CAUTION / BLOCKED** verdict with a human-readable evidence report. Payment is released only after the deliverable is verified.

Built for the **NEAR Hackathon 2026** (Build Beyond Boundaries). Tracks: Agents, Business, Security.

## Why

Naming is a one-way door. Teams routinely pay for a logo, a domain and a launch — then discover the .com belongs to someone else, or that an identical trademark application is already pending. ClearName turns that check into an agent-to-agent service: send a name, get a verdict with evidence, in one job.

## How it works

1. **Check** (`engine/brand_check.py`) — read-only queries to public sources only:
   - Domains (.com / .io / .ai): RDAP + DNS.
   - Social handles: X, Instagram, TikTok profile pages.
   - App stores: iTunes Search API, Google Play public search.
   - Trademarks: USPTO federal records (via the Justia mirror when the official API is unreachable; coverage limits are stated in every report).
2. **Decide** (`engine/verdict.py`) — four surface results become a risk score and a verdict (`clearname-verdict-v1` JSON schema):
   - Trademark conflict, or all checked domains taken → **BLOCKED**
   - Any conflict, or any surface that could not be verified → **CAUTION**
   - No conflicts and every surface verified → **CLEAR**
   - *Unknown is never reported as available.* An unchecked surface can force CAUTION; it can never produce a CLEAR.
3. **Deliver** (`engine/render.py`) — the same result renders to a Markdown report (for humans) and a JSON verdict (for machines); the two can never disagree.
4. **Run** (`agent/runner.py`) — one job end to end: parse brief → verdict → both deliverables → delivery. Frozen guardrails (`agent/guardrails.py`): input validation, price bands that fail closed, a daily job cap, per-job timeouts, and a hash-chained audit log.
5. **Hire** (`buyer/demo_buyer.py`) — a demo buyer that hires the agent and branches on the verdict: CLEAR → accept; BLOCKED → reject and request variants; CAUTION → escalate to a human.

## Demo output (dry-run, offline fixtures)

```
$ python -m agent.runner --dry-run --scenario zenvora
verdict: BLOCKED (risk 85/100)
# .com / .io / .ai all taken; 4 pending US trademark applications
# for the identical mark ZENVORA

$ python -m agent.runner --dry-run --scenario clear
verdict: CLEAR
# name "clearname-demo": domains available, handles available,
# no trademark hits
```

Full sample reports from live checks: [`demo/samples/zenvora-report.md`](demo/samples/zenvora-report.md) (Zenvora — every domain taken, 9 trademark hits in the searched database) and [`demo/samples/nimbus-report.md`](demo/samples/nimbus-report.md).

## Run it

```bash
# dry-run demo (offline, no account, no network)
python -m agent.runner --dry-run --scenario zenvora --out-dir /tmp/out
python -m buyer.demo_buyer --scenario zenvora

# the check engine on its own (read-only public queries)
python3 engine/brand_check.py "Zenvora" --tlds .com,.io,.ai --suggest --out /tmp/report.md

# tests (offline; needs pytest + requests)
python -m pytest tests/ -q   # 49 passed
```

The live marketplace path is deliberately **not** faked: `python -m agent.runner --live` refuses and lists exactly what is missing (an agent API token in `CLEARNAME_AGENT_API_TOKEN` and a reachable MCP endpoint in `CLEARNAME_MCP_ENDPOINT` — see `.env.example`) until the marketplace account steps are done.

## Status & disclosure

- The check engine (`engine/brand_check.py`) and the sample reports predate this repository — they are the working core behind an already-live "Brand Name Checker" listing on the NEAR AI Agent Marketplace, and are included here as the declared baseline, per hackathon rules.
- Built during the hackathon: the verdict layer, the report/verdict renderer, the agent runtime, the demo buyer loop, and the guardrails layer.
- Dry-run mode never touches the network, an account, or a submission; it runs on the fixtures in `engine/fixtures/`.

## Limitations

- X / Instagram handles cannot always be confirmed without login; those surfaces report "couldn't confirm" (→ CAUTION), never a guess.
- Trademark coverage is US federal records; state registrations, common-law use and non-US registers are not covered. Reports are a starting point, not legal advice.
- "Available" for a domain means no RDAP record and no DNS resolution at query time — not a guarantee of registrability.

## License

MIT — see `LICENSE`.
