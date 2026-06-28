# Backlog / Dev Log — GTM Agent Platform

Roadmap of motions × channels × capabilities, each with the eval that makes it trustworthy.
Status: ✅ done · 🔨 in progress · 📋 planned · 💡 idea.

## Motions (the agents)

| Motion | Status | Trigger | Action | Eval |
| --- | --- | --- | --- | --- |
| **Inbound triage** | 🔨 hardening | inbound lead | route + draft + CRM write | held-out tier accuracy (de-gamed): 65.7%, false-cold 12.5% |
| **Outbound campaign** | 📋 centerpiece-next | list / segment | enrich → score → A/B draft → sequence (no-send) | reply-proxy + draft-quality eval |
| Lifecycle marketing | 💡 backlog | product/usage event | next-best-touch | conversion-proxy eval |
| Customer surveys | 💡 backlog | segment / event | survey orchestration | response-rate eval |
| ABX / account-based | 💡 backlog | target account list | account play orchestration | account-engagement eval |

## Channels (input modes → normalized Signal)

| Channel | Status | Notes |
| --- | --- | --- |
| Web form | ✅ | demo surface |
| Webhook (n8n) | ✅ | orchestration entry |
| CSV / list upload | 📋 | JD: "list uploads, audience segmentation"; exercises the async queue |
| Email intake | 📋 | IMAP/Gmail → Signal |
| Chat / Slack | 💡 | conversational intake |

## Adjacent-to-signup capabilities

| Capability | Status | Notes |
| --- | --- | --- |
| Dedup / idempotency | ✅ | SHA-256 key |
| Consent / opt-out | ✅ | intent-driven hard-disqualify |
| Enrichment (PDL waterfall) | ✅ | value/source/confidence |
| Account matching | 📋 | lead → existing opp/account |
| Routing assignment | 📋 | territory / round-robin to a specific AE |
| Slack "hot lead" notify | 📋 | visible agent action — strong demo moment |
| Speed-to-lead SLA | 💡 | time-to-first-touch tracking |

## Inbound hardening — production phases

| Phase | Status | Scope |
| --- | --- | --- |
| D — agency + confidence-gating | 🔨 | branch on real signals; ≥4 trace shapes; fix lemonade false-hot |
| E — LLM extraction + calibration | 📋 | LLM extraction on; calibrate on a NEW dev split; re-run held-out once |
| G — API hardening | 📋 | auth, rate limit, validation, structured errors, timeouts |
| H — reliability | 📋 | async queue, retries + circuit breakers, graceful degradation, PG migrations |
| I — security | 📋 | prompt-injection defense, SSRF guard, secrets, CORS, dep scan |
| J — privacy/compliance | 📋 | PII minimization, retention/deletion, cassette scrub, GDPR posture |
| K — observability | 📋 | health/metrics, error tracking, structured logging, alerting |
| L — testing + CI/CD | 📋 | integration + load tests, GitHub Actions test + eval gate |
| UI template | 📋 | simplified intake (name + optional email + message), ops + eval dashboards, design polish |
| M — merge, deploy, verify, re-audit | 📋 | live demo verified e2e; fresh-eyes re-audit by a separate agent |
| N — docs | 📋 | README honesty, architecture, runbook |

## After inbound is hardened + verified

1. **UI template** — the demo surface.
2. **Outbound campaign motion** — the centerpiece; proves the platform generalizes and maps 1:1 to the JD's "end-to-end campaign execution."
3. (optional) **Spark-style feedback clustering** — same grounded engine on customer feedback → themes → opportunities.

## Definition of done (every motion)

- Ships with a **de-gamed, held-out eval** (labels blind to the rules; no answer-leakage).
- Tuning only on a **separate dev split**; held-out set is **write-once**.
- Every step **traced**; every enriched field carries **source + confidence**.
- Drafts **never send**; side effects are explicit and reversible.

## Enrichment sources (waterfall categories)

The waterfall adds signal by category, each confidence-tagged. Clay is the no-code
orchestrator of exactly this; we build it in code.

| Category | Tool(s) | Status |
| --- | --- | --- |
| Firmographic / person | PDL ✅ · Apollo, Clearbit/Breeze, ZoomInfo 💡 | ✅ + 💡 |
| Email validity / deliverability | MX + disposable ✅ · verifier (catch-all/role: Hunter, ZeroBounce) 📋 | partial |
| Company website read | LLM read on domain (dig_deeper) ✅ | ✅ |
| Tech stack / fit | BuiltWith, Wappalyzer 💡 | 💡 |
| Intent data (3rd-party) | Bombora, 6sense, G2 💡 | 💡 |
| IP → company reveal | Clearbit Reveal, RB2B 💡 (for anonymous visitors, not email leads) | 💡 |

## Inbound surface — full capability map

Beyond score + route, the inbound motion includes (JD-named tools in parens):

| Capability | Status | Notes |
| --- | --- | --- |
| Account matching | 📋 | lead → existing account / open opp |
| Routing & assignment | 📋 | territory, round-robin, named-account ownership |
| Speed-to-lead SLA | 💡 | time-to-first-touch — the core inbound metric |
| Slack hot-lead notify | 📋 | visible agent action; strong demo |
| Instant meeting booking | 💡 | Calendly/Chili Piper (Qualified, Piper) — highest-converting inbound move |
| Conversational qualification | 💡 | inline qualifying bot (Qualified, Drift) |
| Lifecycle entry | 💡 | cold → nurture sequence |
| Attribution | 💡 | which campaign/source drove the lead |
