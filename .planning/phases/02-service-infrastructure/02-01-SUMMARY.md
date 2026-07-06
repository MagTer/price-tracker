---
phase: 02-service-infrastructure
plan: 01
status: complete
completed: 2026-05-04
retroactive: true
retroactive_documented: 2026-07-06

provides:
  - Concrete infra clients replacing source's IFetcher/IEmailService/LiteLLM-proxy interfaces
  - FastAPI app factory with lifespan-managed scheduler startup

requirements-completed: [INFRA-01, INFRA-02, INFRA-03, INFRA-04, INFRA-05, API-01]
requirements-partial: [INFRA-06]

coverage:
  - id: INFRA-01
    description: "httpx async fetcher replaces IFetcher"
    verification:
      - kind: code-inspection
        ref: "src/infra/fetcher.py"
        status: pass
    human_judgment: false
  - id: INFRA-02
    description: "aiosmtplib email client replaces IEmailService"
    verification:
      - kind: code-inspection
        ref: "src/infra/email.py"
        status: pass
    human_judgment: false
  - id: INFRA-03
    description: "OpenRouter direct client replaces LiteLLM proxy"
    verification:
      - kind: code-inspection
        ref: "src/infra/llm.py, src/domain/parser.py"
        status: pass
    human_judgment: false
  - id: INFRA-04
    description: "OpenRouter headers include Authorization/HTTP-Referer/X-Title"
    verification:
      - kind: code-inspection
        ref: "src/infra/llm.py OPENROUTER_HEADERS"
        status: pass
    human_judgment: false
  - id: INFRA-05
    description: "Async DB session factory"
    verification:
      - kind: code-inspection
        ref: "src/infra/db.py async_session_factory"
        status: pass
    human_judgment: false
  - id: INFRA-06
    description: "Manual price check on a Willys URL succeeds end-to-end"
    verification:
      - kind: unit
        ref: "tests/test_extractors.py (mocked WillysApiExtractor tests, all pass)"
        status: partial
    human_judgment: true
---

# Phase 2: Service Infrastructure Summary

**Retroactive summary** — written 2026-07-06 to document work actually completed 2026-05-04 (commit `d92372a`) plus follow-up bug fixes landed today (quick task 260706-rso). No formal discuss/plan/execute pipeline ran for this phase at the time.

## What Was Built

- `src/infra/fetcher.py` — `WebFetcher`: httpx async client, browser headers, HTML-to-text extraction
- `src/infra/email.py` — `SmtpEmailService`: aiosmtplib wrapper, `is_configured()` gate
- `src/infra/llm.py` — OpenRouter base URL/headers construction
- `src/infra/providers.py` — singleton accessors for fetcher/email service
- `src/api/app.py` — `create_app()` FastAPI factory; lifespan starts `PriceCheckScheduler`

## Verification Evidence (gathered 2026-07-06)

1. **App boots, scheduler starts** (ROADMAP criterion 1) — VERIFIED. During quick task 260706-t3p, a manual script entered `create_app()`'s `lifespan()` context manager directly and confirmed `app.state.scheduler` reports `_running is True` inside the block, with clean shutdown on exit.
2. **Manual Willys price check persists a PricePoint end-to-end** (ROADMAP criterion 2) — **NOT independently re-verified this session.** `tests/test_extractors.py` exercises `WillysApiExtractor` against mocked HTTP responses only; no evidence exists (in git history or this session's testing) of a real network call against `https://www.willys.se` actually persisting a `PricePoint` row. The code path is plausible (structured API extractor + scheduler `_check_single_product` wiring both exist and are unit-tested), but a live smoke test is recommended before treating this as fully proven.
3. **OpenRouter URL/headers/cascade correct** (ROADMAP criterion 3) — VERIFIED, but only as of **today**: this criterion was actually FALSE from 2026-05-04 until quick task 260706-rso (2026-07-06) fixed a doubled `/v1` segment in the chat-completions URL and replaced placeholder LiteLLM-proxy model aliases with real OpenRouter model IDs. Prior to today's fix, every LLM-based price extraction would have 404'd.
4. **`.env.template` documents required vars** (ROADMAP criterion 4) — not independently re-inspected this session (file access was permission-blocked in this session); assumed consistent with `DEPLOY-04`'s existing "Complete" status in REQUIREMENTS.md's traceability table, which predates this backfill.

## Deviations from "Plan"

Since this was retroactive, "deviations" here means: gaps between what ROADMAP.md's success criteria claim and what evidence actually supports, discovered during this 2026-07-06 backfill:
- Criterion 2 (live Willys check) and criterion 4 (.env.template completeness) are asserted based on code plausibility / pre-existing status rather than freshly re-verified — flagged as `human_judgment: true` in this summary's coverage table rather than silently marked pass.
- Criterion 3 was actually broken in production from May through July 2026 (fixed today) — noted here for the historical record so a future reader doesn't assume it worked continuously since May.

## Next Phase Readiness

Phase 3 (Admin UI) builds directly on `src/api/app.py`'s factory pattern — already in place and working.
