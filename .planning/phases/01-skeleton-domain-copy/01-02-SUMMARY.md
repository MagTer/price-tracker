---
phase: 01-skeleton-domain-copy
plan: 02
subsystem: domain
tags:
  - python
  - sqlalchemy
  - port
  - verbatim
dependency-graph:
  requires:
    - "src.infra.db.Base + _utc_now (from Plan 01-01)"
    - "src.infra.providers.get_fetcher (from Plan 01-01, stub)"
    - "src.domain.protocols.{IEmailService, IFetcher, EmailMessage} (from Plan 01-01)"
  provides:
    - "src.domain.models.{Store, Product, ProductStore, PricePoint, PriceWatch} with tenant_id"
    - "src.domain.{PriceTrackerService, PriceParser, PriceCheckScheduler, PriceNotifier, PriceExtractionResult}"
    - "src.domain.extractors.{base, willys_api}"
    - "src.domain.stores.get_store_hints"
  affects:
    - "01-03-migration (Wave 2): consumes models.py final shape for the squashed initial migration"
    - "01-04-tests (Wave 2): rebases the 5 source test files against the ported domain modules"
tech-stack:
  added: []
  patterns:
    - "Verbatim port with three documented transforms (D-16): import-path rewrites, context_id -> tenant_id rename, price_tracker_ table-prefix drop"
    - "Free-floating tenant_id UUID column with no FK to a tenants table (D-03/D-10)"
key-files:
  created:
    - "src/domain/__init__.py (30 LOC)"
    - "src/domain/result.py (19 LOC, pure copy)"
    - "src/domain/models.py (157 LOC, 3 transforms applied)"
    - "src/domain/parser.py (226 LOC)"
    - "src/domain/notifier.py (333 LOC)"
    - "src/domain/scheduler.py (487 LOC)"
    - "src/domain/service.py (590 LOC)"
    - "src/domain/extractors/__init__.py (1 LOC, pure copy)"
    - "src/domain/extractors/base.py (20 LOC)"
    - "src/domain/extractors/willys_api.py (101 LOC)"
    - "src/domain/stores/__init__.py (66 LOC, pure copy)"
  modified: []
decisions:
  - "models.py is 157 LOC (vs source's 161 LOC) — the 4-line delta is the natural side effect of the context_id -> tenant_id rename collapsing each ForeignKey mapped_column from 3 lines to 1 line; no other changes were made"
metrics:
  duration: "~12 min"
  completed: "2026-05-02"
  tasks: 2
  files: 11
---

# Phase 1 Plan 02: Domain Verbatim Port Summary

Ported all 11 modules from `ai-agent-platform/services/agent/src/modules/price_tracker/` into `src/domain/` with only the three documented transforms (D-16): import-path rewrites, `context_id` -> `tenant_id` column rename (with the FK to `contexts.id` dropped per D-03), and the `price_tracker_` table-prefix drop (DB-04). All five public domain symbols are importable via `from domain import ...`. Plan 03 (squashed migration) and Plan 04 (test rebase) can now both consume the final `src/domain/models.py` shape.

## What Was Built

11 ported domain files (2,030 target LOC vs 2,034 source LOC; the 4-line delta is the column-rename collapse in `models.py`):

| Artifact | Source LOC | Target LOC | Role |
|----------|-----------:|-----------:|------|
| `src/domain/__init__.py` | 30 | 30 | Public re-exports of `PriceTrackerService`, `PriceParser`, `PriceExtractionResult`, `PriceNotifier`, `PriceCheckScheduler` |
| `src/domain/result.py` | 19 | 19 | `PriceExtractionResult` dataclass |
| `src/domain/models.py` | 161 | 157 | ORM: Store, Product, ProductStore, PricePoint, PriceWatch (with `tenant_id`) |
| `src/domain/parser.py` | 226 | 226 | LLM-based `PriceParser` with cascade strategy + LITELLM URL preserved |
| `src/domain/notifier.py` | 333 | 333 | `PriceNotifier` for alert + weekly summary HTML emails |
| `src/domain/scheduler.py` | 487 | 487 | `PriceCheckScheduler` background loop |
| `src/domain/service.py` | 590 | 590 | `PriceTrackerService` orchestration + `tenant_id` propagation |
| `src/domain/extractors/__init__.py` | 1 | 1 | Subpackage marker |
| `src/domain/extractors/base.py` | 20 | 20 | `PriceExtractor` Protocol |
| `src/domain/extractors/willys_api.py` | 101 | 101 | `WillysApiExtractor` |
| `src/domain/stores/__init__.py` | 66 | 66 | `get_store_hints` + 5 `*_HINTS` constants |
| **TOTAL** | **2,034** | **2,030** | |

## Verbatim Port Mapping Table (D-16)

| Target path | Source path (modules/price_tracker/) | LOC | Changes from source |
|---|---|---:|---|
| `src/domain/__init__.py` | `__init__.py` | 30 | 4 imports rewritten (`modules.price_tracker.{notifier,parser,scheduler,service}` -> `domain.{notifier,parser,scheduler,service}`); `__all__` and `get_price_tracker` placeholder body byte-identical |
| `src/domain/result.py` | `result.py` | 19 | NONE - pure copy |
| `src/domain/models.py` | `models.py` | 157 | (a) `from core.db.models import Base, _utc_now` -> `from infra.db import Base, _utc_now`; (b) `Product.context_id` -> `tenant_id` + drop FK to `contexts.id` (D-03); (c) `PriceWatch.context_id` -> `tenant_id` + drop FK; (d) two docstring lines `"Multi-tenant: scoped to context_id."` -> `"Multi-tenant: scoped to tenant_id."`; (e) 5 `__tablename__` values + 3 intra-FK refs lose `price_tracker_` prefix (DB-04). LOC delta -4 because each multi-line `ForeignKey("contexts.id"...) index=True)` mapped_column collapses to a single-line `mapped_column(UUID(as_uuid=True), index=True)`. |
| `src/domain/parser.py` | `parser.py` | 226 | 4 imports rewritten (3 module-level: `extractors.base`, `extractors.willys_api`, `result`; 1 lazy inside `_load_store_hints`: `stores`). LITELLM URL constant + `/v1/chat/completions` POST LEFT UNCHANGED (Phase 2 swap, REQ INFRA-03/04) |
| `src/domain/notifier.py` | `notifier.py` | 333 | 1 import rewritten (`core.protocols.email` -> `domain.protocols.email`); body byte-identical |
| `src/domain/scheduler.py` | `scheduler.py` | 487 | 4 imports rewritten (`core.protocols`, `modules.price_tracker.{models,notifier,parser}`); zero `context_id` symbol references existed in source so no symbol propagation needed |
| `src/domain/service.py` | `service.py` | 590 | 3 imports rewritten (`core.providers` -> `infra.providers`; `modules.price_tracker.{models,parser}` -> `domain.{models,parser}`); 7 `context_id` references propagated to `tenant_id` (see Symbol Propagation table below) |
| `src/domain/extractors/__init__.py` | `extractors/__init__.py` | 1 | NONE - pure copy |
| `src/domain/extractors/base.py` | `extractors/base.py` | 20 | 1 TYPE_CHECKING import rewritten (`modules.price_tracker.result` -> `domain.result`) |
| `src/domain/extractors/willys_api.py` | `extractors/willys_api.py` | 101 | 1 import rewritten (`modules.price_tracker.result` -> `domain.result`) |
| `src/domain/stores/__init__.py` | `stores/__init__.py` | 66 | NONE - pure copy |
| **TOTAL** | | **2,030** | |

## context_id -> tenant_id Symbol Propagation

### scheduler.py
- Source `grep -c context_id`: **0**
- Target `grep -c context_id`: **0**
- No propagation needed. The scheduler operates on ORM model symbols (`PriceWatch.product_id`, `joinedload(PriceWatch.product)`) and never references the renamed column directly.

### service.py
- Source `grep -c context_id`: **7** (3 code, 4 docstring)
- Target `grep -c context_id`: **0**
- All 7 lines rewritten to `tenant_id` (verified by `grep -n tenant_id src/domain/service.py`):

| Source line | Site | Rewrite |
|---|---|---|
| 303 | `create_product` parameter | `context_id: uuid.UUID,` -> `tenant_id: uuid.UUID,` |
| 321 | `create_product` docstring | `context_id: Context UUID for multi-tenancy.` -> `tenant_id: Context UUID for multi-tenancy.` |
| 334 | `create_product` body kwarg to `Product(...)` | `context_id=context_id,` -> `tenant_id=tenant_id,` |
| 395 | `create_watch` parameter | `context_id: str,` -> `tenant_id: str,` |
| 407 | `create_watch` docstring | `context_id: UUID string of the context (multi-tenant).` -> `tenant_id: UUID string of the context (multi-tenant).` |
| 420 | `create_watch` body local var | `context_uuid = uuid.UUID(context_id)` -> `tenant_uuid = uuid.UUID(tenant_id)` |
| 424 | `create_watch` body kwarg to `PriceWatch(...)` | `context_id=context_uuid,` -> `tenant_id=tenant_uuid,` |

The docstring inner phrasing ("Context UUID for multi-tenancy", "UUID string of the context") was preserved verbatim - per the verbatim-port contract, we change only the symbol name, not surrounding English prose.

## LiteLLM Constant Preservation (Phase 2 boundary)

`src/domain/parser.py` retains:

- Line 19: `LITELLM_API_BASE = os.getenv("LITELLM_API_BASE", "http://litellm:4000")`
- Line 184: `f"{LITELLM_API_BASE}/v1/chat/completions"` inside `_extract_with_model`

Both are preserved verbatim per the plan and CLAUDE.md "preserve constants whose names look 'wrong' for this repo - the rename happens later (Phase 2 boundary)". REQ INFRA-03/04 will swap this for OpenRouter direct.

## Acceptance Criteria Met

Task 1 (leaf modules):
- [x] All 6 files exist
- [x] `grep -c context_id src/domain/models.py` returns 0
- [x] `grep -c tenant_id src/domain/models.py` returns 4 (>= 2)
- [x] `grep -c 'ForeignKey("contexts.id"' src/domain/models.py` returns 0 (D-03)
- [x] `grep -c price_tracker_ src/domain/models.py` returns 0 (DB-04)
- [x] 5 `__tablename__` values match `(stores|products|product_stores|price_points|watches)`
- [x] `from infra.db import Base, _utc_now` present in models.py
- [x] No `from modules.price_tracker` recursive across `src/domain/`
- [x] No `from core.` recursive across `src/domain/`
- [x] `DOZ_HINTS` defined and referenced in `stores/__init__.py`
- [x] LOC parity: result.py=19, extractors/__init__.py=1, extractors/willys_api.py=101, stores/__init__.py=66
- [x] Verify Python one-liner exits 0

Task 2 (logic modules):
- [x] All 5 files exist
- [x] No `from modules.price_tracker` anywhere in `src/domain/`
- [x] No `from core.` anywhere in `src/domain/`
- [x] `grep -c context_id src/domain/service.py` returns 0
- [x] `grep -c context_id src/domain/scheduler.py` returns 0
- [x] One `class PriceParser` / `class PriceNotifier` / `class PriceCheckScheduler` / `class PriceTrackerService`
- [x] `from infra.providers import get_fetcher` in service.py
- [x] `LITELLM_API_BASE` referenced twice in parser.py (constant + usage)
- [x] LOC parity: notifier.py=333, __init__.py=30, parser.py=226, scheduler.py=487, service.py=590
- [x] Verify Python one-liner exits 0
- [x] SUMMARY.md exists with the verbatim port mapping table (this file)

Plan-level verification block:
- [x] All 4 import-cleanliness greps return 0
- [x] `grep -c price_tracker_ src/domain/models.py` returns 0
- [x] `from domain import PriceTrackerService, PriceParser, PriceCheckScheduler, PriceNotifier, PriceExtractionResult` succeeds
- [x] All 4 pure-copy LOC parity checks pass
- [x] "Verbatim Port Mapping" present in SUMMARY.md

## Commits

| Hash | Subject |
|------|---------|
| `743c93e` | feat(01-02): port domain leaf modules verbatim from source |
| `4e15d4c` | feat(01-02): port domain logic modules verbatim from source |

## Deviations from Plan

None - plan executed exactly as written.

The expected `models.py` LOC delta (-4 lines) is captured in the port mapping table as a transparent side-effect of the documented `context_id` -> `tenant_id` transform; it is not an extra change. No source file required a transform outside the three documented categories.

## Source-Line Surprises

- **scheduler.py had zero `context_id` references in the source**, contradicting the plan's caution that propagation might be needed. The plan correctly anticipated this with "verify with grep" - no action required.
- **service.py docstrings refer to "Context UUID" / "UUID string of the context"** in English prose at lines 321 and 407. Per the verbatim-port contract, we rename only the symbol (`context_id` -> `tenant_id`), not surrounding prose. The docstrings now read `tenant_id: Context UUID for multi-tenancy.` and `tenant_id: UUID string of the context (multi-tenant).` - intentional; English copy is part of the source's voice and we are not editing it during the port (CLAUDE.md "preserve the source repo's conventions"). Polish would be a Phase 6 backlog item.
- **service.py and scheduler.py both define a local `_utc_now()` helper** - service.py at line 22-27 redefines it (returns naive UTC), and scheduler.py uses inline `datetime.now(UTC).replace(tzinfo=None)` rather than importing the helper. This is a known shortcoming in the source (CLAUDE.md "don't fix known shortcomings during the port") and is preserved verbatim.

## Self-Check: PASSED

Files (all 11 confirmed present):
- FOUND: src/domain/__init__.py
- FOUND: src/domain/result.py
- FOUND: src/domain/models.py
- FOUND: src/domain/parser.py
- FOUND: src/domain/notifier.py
- FOUND: src/domain/scheduler.py
- FOUND: src/domain/service.py
- FOUND: src/domain/extractors/__init__.py
- FOUND: src/domain/extractors/base.py
- FOUND: src/domain/extractors/willys_api.py
- FOUND: src/domain/stores/__init__.py

Commits (both confirmed in `git log`):
- FOUND: 743c93e
- FOUND: 4e15d4c
