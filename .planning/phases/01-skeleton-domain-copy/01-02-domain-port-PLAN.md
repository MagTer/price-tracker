---
phase: 01-skeleton-domain-copy
plan: 02
type: execute
wave: 2
depends_on: [01-01]
files_modified:
  - src/domain/__init__.py
  - src/domain/result.py
  - src/domain/models.py
  - src/domain/parser.py
  - src/domain/notifier.py
  - src/domain/scheduler.py
  - src/domain/service.py
  - src/domain/extractors/__init__.py
  - src/domain/extractors/base.py
  - src/domain/extractors/willys_api.py
  - src/domain/stores/__init__.py
autonomous: true
requirements:
  - DOMAIN-01
  - DOMAIN-02
  - DOMAIN-03
user_setup: []
tags:
  - python
  - sqlalchemy
  - port
  - verbatim

must_haves:
  truths:
    - "All 11 domain files exist under src/domain/ with content byte-equivalent to source modulo three documented transforms (D-16): import-path rewrites, context_id -> tenant_id rename (drop FK to contexts), and price_tracker_ table-prefix drop (DB-04)"
    - "models.py renames context_id (Product, PriceWatch) to tenant_id with the same UUID type, and DROPS the FK to contexts.id (D-03 — no tenants table; tenant_id is a free-floating UUID)"
    - "Verbatim-port mapping table (D-16) is recorded in 01-02-SUMMARY.md so the audit trail says exactly which lines changed per file"
    - "Source `from modules.price_tracker.X` rewrites uniformly to `from domain.X` (src-layout, D-15)"
    - "Source `from core.db.models import Base, _utc_now` rewrites to `from infra.db import Base, _utc_now`"
    - "Source `from core.protocols.email import ...` rewrites to `from domain.protocols.email import ...`"
    - "Source `from core.protocols import IEmailService, IFetcher` rewrites to `from domain.protocols import IEmailService, IFetcher`"
    - "Source `from core.providers import get_fetcher` rewrites to `from infra.providers import get_fetcher` (Phase 2 swaps the body for a real httpx fetcher)"
    - "All ported modules importable: `python -c 'from domain import PriceTrackerService, PriceParser, PriceCheckScheduler, PriceNotifier, PriceExtractionResult'` succeeds"
  artifacts:
    - path: "src/domain/models.py"
      provides: "ORM models (Store, Product, ProductStore, PricePoint, PriceWatch) with tenant_id"
      contains: "tenant_id"
    - path: "src/domain/parser.py"
      provides: "PriceParser + PriceExtractionResult re-export"
      contains: "class PriceParser"
    - path: "src/domain/scheduler.py"
      provides: "PriceCheckScheduler"
      contains: "class PriceCheckScheduler"
    - path: "src/domain/service.py"
      provides: "PriceTrackerService"
      contains: "class PriceTrackerService"
    - path: "src/domain/notifier.py"
      provides: "PriceNotifier"
      contains: "class PriceNotifier"
    - path: "src/domain/result.py"
      provides: "PriceExtractionResult dataclass"
      contains: "class PriceExtractionResult"
    - path: "src/domain/extractors/willys_api.py"
      provides: "WillysApiExtractor"
      contains: "class WillysApiExtractor"
    - path: "src/domain/stores/__init__.py"
      provides: "get_store_hints + per-store hint constants"
      contains: "DOZ_HINTS"
  key_links:
    - from: "src/domain/parser.py"
      to: "src/domain/extractors/willys_api.py"
      via: "from domain.extractors.willys_api import WillysApiExtractor"
      pattern: "domain\\.extractors\\.willys_api"
    - from: "src/domain/parser.py"
      to: "src/domain/stores/__init__.py"
      via: "from domain.stores import get_store_hints (lazy import inside _load_store_hints)"
      pattern: "domain\\.stores"
    - from: "src/domain/scheduler.py"
      to: "src/domain/notifier.py"
      via: "from domain.notifier import PriceNotifier"
      pattern: "domain\\.notifier"
    - from: "src/domain/service.py"
      to: "src/infra/providers.py"
      via: "from infra.providers import get_fetcher (Phase 2 placeholder)"
      pattern: "infra\\.providers"
    - from: "src/domain/models.py"
      to: "src/infra/db.py"
      via: "from infra.db import Base, _utc_now"
      pattern: "infra\\.db"
---

<objective>
Verbatim port the price-tracker domain modules from `ai-agent-platform/services/agent/src/modules/price_tracker/` into `src/domain/` with **only three documented transforms** (D-16):

1. **Import-path rewrites:** `modules.price_tracker.X` -> `domain.X`; `core.db.models` -> `infra.db`; `core.protocols.email` -> `domain.protocols.email`; `core.protocols` -> `domain.protocols`; `core.providers` -> `infra.providers`.
2. **Column rename:** `context_id` -> `tenant_id` on `Product` and `PriceWatch` ORM models, INCLUDING dropping the `ForeignKey("contexts.id", ondelete="CASCADE")` (D-03 — no `contexts`/`tenants` table in this repo). Propagate the symbol rename to every reference inside `service.py` (and `scheduler.py` if any).
3. **Table prefix drop (DB-04):** `__tablename__` values and intra-FK refs lose the `price_tracker_` prefix.

ANY other change is forbidden in this port. SQLAlchemy 2.0 syntax already in source (no rewrite needed). Cascading deletes preserved per PROJECT.md "behavioral parity" constraint.

Purpose: REQ DOMAIN-01, DOMAIN-02, DOMAIN-03. Goal of the extraction is byte-equivalent feature parity in a standalone repo.

Output: 11 ported files in `src/domain/` plus the verbatim port mapping table in 01-02-SUMMARY.md. After this plan, both Plan 03 (squashed migration — needs `models.py` final shape) and Plan 04 (test rebase — needs all domain modules) can proceed in Wave 2.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/phases/01-skeleton-domain-copy/01-CONTEXT.md
@EXTRACTION.md
@CLAUDE.md

<interfaces>
Source -> target import rewrites (apply uniformly across all ported files):

| Source import | Rewritten import |
|---|---|
| `from core.db.models import Base, _utc_now` | `from infra.db import Base, _utc_now` |
| `from core.protocols.email import EmailMessage, IEmailService` | `from domain.protocols.email import EmailMessage, IEmailService` |
| `from core.protocols import IEmailService, IFetcher` | `from domain.protocols import IEmailService, IFetcher` |
| `from core.providers import get_fetcher` | `from infra.providers import get_fetcher` |
| `from modules.price_tracker.notifier import PriceNotifier` | `from domain.notifier import PriceNotifier` |
| `from modules.price_tracker.parser import (...)` | `from domain.parser import (...)` |
| `from modules.price_tracker.scheduler import PriceCheckScheduler` | `from domain.scheduler import PriceCheckScheduler` |
| `from modules.price_tracker.service import PriceTrackerService` | `from domain.service import PriceTrackerService` |
| `from modules.price_tracker.models import (...)` | `from domain.models import (...)` |
| `from modules.price_tracker.extractors.base import PriceExtractor` | `from domain.extractors.base import PriceExtractor` |
| `from modules.price_tracker.extractors.willys_api import WillysApiExtractor` | `from domain.extractors.willys_api import WillysApiExtractor` |
| `from modules.price_tracker.result import PriceExtractionResult` | `from domain.result import PriceExtractionResult` |
| `from modules.price_tracker.stores import get_store_hints` (lazy, inside parser._load_store_hints) | `from domain.stores import get_store_hints` |

Column rename (models.py only — affects 2 model classes):

| Source line shape | Target line shape |
|---|---|
| `context_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("contexts.id", ondelete="CASCADE"), index=True)` (Product, PriceWatch) | `tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)` |

Symbol-level propagation: every `Product.context_id` / `PriceWatch.context_id` / kwarg `context_id=...` / parameter `context_id: uuid.UUID` inside service.py (and scheduler.py if any) becomes `tenant_id`.

Table prefix drop (models.py only):

| Source `__tablename__` | Target `__tablename__` |
|---|---|
| `"price_tracker_stores"` | `"stores"` |
| `"price_tracker_products"` | `"products"` |
| `"price_tracker_product_stores"` | `"product_stores"` |
| `"price_tracker_price_points"` | `"price_points"` |
| `"price_tracker_watches"` | `"watches"` |

And inside `mapped_column(ForeignKey("price_tracker_X.id"), ...)` the `price_tracker_` prefix drops.

Source files (LOC verified) at `/home/magnus/dev/ai-agent-platform/services/agent/src/modules/price_tracker/`:

```
__init__.py              30 LOC
result.py                19 LOC
models.py               161 LOC  (column rename + prefix drop targets)
parser.py               226 LOC
notifier.py             333 LOC
scheduler.py            487 LOC
service.py              590 LOC
extractors/__init__.py    1 LOC
extractors/base.py       20 LOC
extractors/willys_api.py 101 LOC
stores/__init__.py       66 LOC
TOTAL                  2034 LOC
```

NOTE on parser.py LiteLLM constant: `LITELLM_API_BASE = os.getenv("LITELLM_API_BASE", "http://litellm:4000")` and `_extract_with_model` posts to `f"{LITELLM_API_BASE}/v1/chat/completions"`. DO NOT touch this in Phase 1 — Phase 2 (REQ INFRA-03/04) is where it switches to OpenRouter. Verbatim port preserves the LiteLLM call.
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Port leaf modules (no domain-internal deps)</name>
  <files>src/domain/result.py, src/domain/extractors/__init__.py, src/domain/extractors/base.py, src/domain/extractors/willys_api.py, src/domain/stores/__init__.py, src/domain/models.py</files>
  <read_first>
    - /home/magnus/dev/ai-agent-platform/services/agent/src/modules/price_tracker/result.py (19 LOC, no rewrites)
    - /home/magnus/dev/ai-agent-platform/services/agent/src/modules/price_tracker/extractors/__init__.py (1 LOC)
    - /home/magnus/dev/ai-agent-platform/services/agent/src/modules/price_tracker/extractors/base.py (20 LOC, TYPE_CHECKING import rewrite)
    - /home/magnus/dev/ai-agent-platform/services/agent/src/modules/price_tracker/extractors/willys_api.py (101 LOC, single import rewrite)
    - /home/magnus/dev/ai-agent-platform/services/agent/src/modules/price_tracker/stores/__init__.py (66 LOC, no rewrites)
    - /home/magnus/dev/ai-agent-platform/services/agent/src/modules/price_tracker/models.py (161 LOC, all three transforms)
    - src/infra/db.py (created in Plan 01 — confirms Base + _utc_now exist)
  </read_first>
  <action>
Port these 6 files in this order (none depends on the next within this task):

1. `src/domain/result.py` — copy `/home/magnus/dev/ai-agent-platform/services/agent/src/modules/price_tracker/result.py` BYTE-FOR-BYTE. No imports to rewrite. 19 LOC.

2. `src/domain/extractors/__init__.py` — copy verbatim. Single line: `"""Store-specific price extractor implementations."""`. 1 LOC.

3. `src/domain/extractors/base.py` — copy from source (20 LOC). Apply rewrite: line 8 `from modules.price_tracker.result import PriceExtractionResult` becomes `from domain.result import PriceExtractionResult`.

4. `src/domain/extractors/willys_api.py` — copy from source (101 LOC). Apply rewrite: line 9 `from modules.price_tracker.result import PriceExtractionResult` becomes `from domain.result import PriceExtractionResult`.

5. `src/domain/stores/__init__.py` — copy `/home/magnus/dev/ai-agent-platform/services/agent/src/modules/price_tracker/stores/__init__.py` BYTE-FOR-BYTE. 66 LOC. No rewrites.

6. `src/domain/models.py` — copy from source (161 LOC) and apply ALL THREE transforms:

   (a) Import rewrite (line 12 in source): `from core.db.models import Base, _utc_now` becomes `from infra.db import Base, _utc_now`.

   (b) Column rename (Product class, lines ~49-51 in source):
   ```
   BEFORE:
   context_id: Mapped[uuid.UUID] = mapped_column(
       ForeignKey("contexts.id", ondelete="CASCADE"), index=True
   )
   AFTER:
   tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
   ```
   Same transform on `PriceWatch` (lines ~143-146 in source).

   (c) Two docstring lines say "Multi-tenant: scoped to context_id." Update them to "Multi-tenant: scoped to tenant_id."

   (d) Table prefix drop — the five `__tablename__` values and the three intra-FK `ForeignKey("price_tracker_*.id")` references all lose `price_tracker_`:
   - `"price_tracker_stores"` -> `"stores"`
   - `"price_tracker_products"` -> `"products"`
   - `"price_tracker_product_stores"` -> `"product_stores"`
   - `"price_tracker_price_points"` -> `"price_points"`
   - `"price_tracker_watches"` -> `"watches"`
   - `ForeignKey("price_tracker_products.id")` -> `ForeignKey("products.id")` (in ProductStore + PriceWatch)
   - `ForeignKey("price_tracker_stores.id")` -> `ForeignKey("stores.id")` (in ProductStore)
   - `ForeignKey("price_tracker_product_stores.id")` -> `ForeignKey("product_stores.id")` (in PricePoint)

   Do NOT change anything else: not column types, not the cascading-delete relationships, not the SQLAlchemy 2.0 `Mapped[]` syntax.
  </action>
  <verify>
    <automated>poetry run python -c "from domain.result import PriceExtractionResult; from domain.extractors.willys_api import WillysApiExtractor; from domain.extractors.base import PriceExtractor; from domain.stores import get_store_hints; from domain.models import Store, Product, ProductStore, PricePoint, PriceWatch; assert hasattr(Product, 'tenant_id') and not hasattr(Product, 'context_id'); assert hasattr(PriceWatch, 'tenant_id') and not hasattr(PriceWatch, 'context_id'); assert Store.__tablename__ == 'stores' and PriceWatch.__tablename__ == 'watches' and Product.__tablename__ == 'products' and ProductStore.__tablename__ == 'product_stores' and PricePoint.__tablename__ == 'price_points'; hints = get_store_hints(); assert set(hints.keys()) == {'ica', 'willys', 'apotea', 'med24', 'doz'}; print('leaf modules OK')"</automated>
  </verify>
  <acceptance_criteria>
- All 6 files exist
- `grep -c context_id src/domain/models.py` returns 0
- `grep -c tenant_id src/domain/models.py` returns 2 or more
- `grep -c 'ForeignKey("contexts.id"' src/domain/models.py` returns 0 (D-03 — FK to contexts dropped)
- `grep -c price_tracker_ src/domain/models.py` returns 0 (DB-04 — table prefix dropped)
- `grep -E "__tablename__ = \"(stores|products|product_stores|price_points|watches)\"" src/domain/models.py | wc -l` returns 5
- `grep -c "from infra.db import Base, _utc_now" src/domain/models.py` returns 1
- `grep -rn "from modules.price_tracker" src/domain/ | wc -l` returns 0 (recursive on the 6 ported files)
- `grep -rn "from core\." src/domain/ | wc -l` returns 0
- `grep -c "DOZ_HINTS" src/domain/stores/__init__.py` returns 2 or more (defined + referenced in `get_store_hints`)
- `wc -l < src/domain/result.py` returns 19 (verbatim LOC parity)
- `wc -l < src/domain/extractors/willys_api.py` returns 101 (verbatim LOC parity)
- `wc -l < src/domain/stores/__init__.py` returns 66 (verbatim LOC parity)
- The verify Python one-liner exits 0
  </acceptance_criteria>
  <done>Leaf modules ported. Dependency frontier for Task 2 (parser/notifier/scheduler/service/__init__) is in place.</done>
</task>

<task type="auto">
  <name>Task 2: Port logic modules and write port mapping table</name>
  <files>src/domain/parser.py, src/domain/notifier.py, src/domain/scheduler.py, src/domain/service.py, src/domain/__init__.py</files>
  <read_first>
    - /home/magnus/dev/ai-agent-platform/services/agent/src/modules/price_tracker/parser.py (226 LOC)
    - /home/magnus/dev/ai-agent-platform/services/agent/src/modules/price_tracker/notifier.py (333 LOC)
    - /home/magnus/dev/ai-agent-platform/services/agent/src/modules/price_tracker/scheduler.py (487 LOC)
    - /home/magnus/dev/ai-agent-platform/services/agent/src/modules/price_tracker/service.py (590 LOC)
    - /home/magnus/dev/ai-agent-platform/services/agent/src/modules/price_tracker/__init__.py (30 LOC)
    - src/domain/models.py (Task 1 — confirms ORM symbols available)
    - src/domain/protocols/__init__.py (Plan 01 — confirms IEmailService, IFetcher exports)
    - src/infra/providers.py (Plan 01 — confirms get_fetcher exists as a stub)
  </read_first>
  <action>
Port the remaining 5 files. For each: copy source byte-for-byte and apply ONLY the import rewrites listed in the `<interfaces>` mapping table.

1. `src/domain/parser.py` — port from source (226 LOC). Rewrites:
   - Line 10: `from modules.price_tracker.extractors.base import PriceExtractor` -> `from domain.extractors.base import PriceExtractor`
   - Line 11: `from modules.price_tracker.extractors.willys_api import WillysApiExtractor` -> `from domain.extractors.willys_api import WillysApiExtractor`
   - Line 12: `from modules.price_tracker.result import PriceExtractionResult` -> `from domain.result import PriceExtractionResult`
   - Line 48 (inside `_load_store_hints`): `from modules.price_tracker.stores import get_store_hints` -> `from domain.stores import get_store_hints`

   PRESERVE `LITELLM_API_BASE` constant and the LiteLLM `/v1/chat/completions` POST. Phase 2 swaps these for OpenRouter.

2. `src/domain/notifier.py` — port from source (333 LOC). Rewrites:
   - Line 9: `from core.protocols.email import EmailMessage, IEmailService` -> `from domain.protocols.email import EmailMessage, IEmailService`

3. `src/domain/scheduler.py` — port from source (487 LOC). Rewrites:
   - Line 13: `from core.protocols import IEmailService, IFetcher` -> `from domain.protocols import IEmailService, IFetcher`
   - Lines 14-20 (multi-line import of models): `from modules.price_tracker.models import (PricePoint, PriceWatch, Product, ProductStore, Store)` -> `from domain.models import (PricePoint, PriceWatch, Product, ProductStore, Store)`
   - Line 21: `from modules.price_tracker.notifier import PriceNotifier` -> `from domain.notifier import PriceNotifier`
   - Line 22: `from modules.price_tracker.parser import PriceExtractionResult, PriceParser` -> `from domain.parser import PriceExtractionResult, PriceParser`
   - Symbol propagation: run `grep -n context_id src/domain/scheduler.py` AFTER the copy. If any matches, rewrite each `Product.context_id`/`PriceWatch.context_id`/kwarg `context_id=...` to `tenant_id`. Final `grep -c context_id src/domain/scheduler.py` MUST return 0.

4. `src/domain/service.py` — port from source (590 LOC). Rewrites:
   - Line 15: `from core.providers import get_fetcher` -> `from infra.providers import get_fetcher`
   - Line 16: `from modules.price_tracker.models import PricePoint, PriceWatch, Product, ProductStore, Store` -> `from domain.models import PricePoint, PriceWatch, Product, ProductStore, Store`
   - Line 17: `from modules.price_tracker.parser import PriceParser` -> `from domain.parser import PriceParser`
   - Symbol propagation: every `Product.context_id` / `PriceWatch.context_id` / kwarg `context_id=...` / parameter `context_id: uuid.UUID` becomes `tenant_id`. Final `grep -c context_id src/domain/service.py` MUST return 0.

   service.py at line ~484 calls `fetcher = get_fetcher()` which raises `NotImplementedError` in Phase 1 (Plan 01's stub). Tests are mocked at the session/fetcher boundary; Phase 2 replaces the stub.

5. `src/domain/__init__.py` — port from source (30 LOC). Rewrites lines 7-10:
   - `from modules.price_tracker.notifier import PriceNotifier` -> `from domain.notifier import PriceNotifier`
   - `from modules.price_tracker.parser import PriceExtractionResult, PriceParser` -> `from domain.parser import PriceExtractionResult, PriceParser`
   - `from modules.price_tracker.scheduler import PriceCheckScheduler` -> `from domain.scheduler import PriceCheckScheduler`
   - `from modules.price_tracker.service import PriceTrackerService` -> `from domain.service import PriceTrackerService`

   Keep `__all__` and `get_price_tracker()` `NotImplementedError` placeholder verbatim.

After ports complete, write `01-02-SUMMARY.md` containing the **Verbatim Port Mapping Table** (D-16):

| Target path | Source path (modules/price_tracker/) | LOC | Changes from source |
|---|---|---|---|
| src/domain/__init__.py | __init__.py | 30 | 4 imports rewritten; body byte-identical |
| src/domain/result.py | result.py | 19 | NONE — pure copy |
| src/domain/models.py | models.py | 161 | (a) `from core.db.models` -> `from infra.db`; (b) Product.context_id -> tenant_id + drop FK to contexts.id (D-03); (c) PriceWatch.context_id -> tenant_id + drop FK; (d) two docstring "context_id" -> "tenant_id"; (e) 5 __tablename__ values + 3 intra-FK refs lose price_tracker_ prefix (DB-04) |
| src/domain/parser.py | parser.py | 226 | 4 imports rewritten; LiteLLM URL constant LEFT UNCHANGED (Phase 2 swap) |
| src/domain/notifier.py | notifier.py | 333 | 1 import rewritten (core.protocols.email -> domain.protocols.email); body byte-identical |
| src/domain/scheduler.py | scheduler.py | 487 | 4 imports rewritten; context_id symbol references -> tenant_id if any (verify with grep) |
| src/domain/service.py | service.py | 590 | 3 imports rewritten; ALL context_id symbol references -> tenant_id (column rename propagation); body otherwise byte-identical |
| src/domain/extractors/__init__.py | extractors/__init__.py | 1 | NONE — pure copy |
| src/domain/extractors/base.py | extractors/base.py | 20 | 1 TYPE_CHECKING import rewritten |
| src/domain/extractors/willys_api.py | extractors/willys_api.py | 101 | 1 import rewritten |
| src/domain/stores/__init__.py | stores/__init__.py | 66 | NONE — pure copy |
| TOTAL | | 2034 | |

ANY change beyond the "Changes from source" column violates the verbatim-port contract — flag it in the SUMMARY with justification.
  </action>
  <verify>
    <automated>poetry run python -c "from domain import PriceTrackerService, PriceParser, PriceCheckScheduler, PriceNotifier, PriceExtractionResult; from domain.scheduler import PriceCheckScheduler as S2; from domain.service import PriceTrackerService as PT2; from domain.notifier import PriceNotifier as PN2; from domain.parser import PriceParser as PP2; assert PriceTrackerService is PT2 and PriceParser is PP2 and PriceCheckScheduler is S2 and PriceNotifier is PN2; print('logic modules OK')"</automated>
  </verify>
  <acceptance_criteria>
- All 5 files exist
- `grep -rn "from modules.price_tracker" src/domain/ | wc -l` returns 0 (recursive across all 11 ported files)
- `grep -rn "from core\." src/domain/ | wc -l` returns 0
- `grep -c context_id src/domain/service.py` returns 0
- `grep -c context_id src/domain/scheduler.py` returns 0
- `grep -c "class PriceParser" src/domain/parser.py` returns 1
- `grep -c "class PriceNotifier" src/domain/notifier.py` returns 1
- `grep -c "class PriceCheckScheduler" src/domain/scheduler.py` returns 1
- `grep -c "class PriceTrackerService" src/domain/service.py` returns 1
- `grep -c "from infra.providers import get_fetcher" src/domain/service.py` returns 1
- `grep -c "LITELLM_API_BASE" src/domain/parser.py` returns 2 (constant + usage — preserved verbatim)
- `wc -l < src/domain/notifier.py` returns 333 (verbatim LOC parity, single-line import rewrite preserves count)
- `wc -l < src/domain/__init__.py` returns 30
- The verify Python one-liner exits 0
- `.planning/phases/01-skeleton-domain-copy/01-02-SUMMARY.md` exists and contains the port mapping table with all 11 rows + 2034 LOC total
  </acceptance_criteria>
  <done>All 11 domain files ported with three documented transforms only. SUMMARY contains the verbatim port mapping table. Plan 03 (migration) and Plan 04 (tests) can now both consume `src/domain/models.py` final shape.</done>
</task>

</tasks>

<threat_model>
## Trust Boundaries

| Boundary | Description |
|----------|-------------|
| n/a (Phase 1) | This plan ships ported domain code only — no network surface, no auth, no user input. The trust boundary at parser.py LITELLM_API_BASE (HTTP call to a model proxy) is preserved verbatim from source; Phase 2 will swap it for OpenRouter HTTPS with bearer auth. |

## STRIDE Threat Register

| Threat ID | Category | Component | Disposition | Mitigation Plan |
|-----------|----------|-----------|-------------|-----------------|
| T-01-04 | Tampering | `src/domain/models.py` table-name + column-rename transforms | mitigate | Plan 03's migration must use the SAME table names + column shape; Plan 04's tests assert ORM behavior. Three-way cross-check (models <-> migration <-> tests) is the audit. Severity: low. |
| T-01-05 | Repudiation | Verbatim-port mapping table in SUMMARY | mitigate | The mapping table records every change per file. If anyone later asks "did we modify scheduler.py?", the SUMMARY answers it. Severity: info. |
| T-01-06 | Information Disclosure | Hardcoded `sv-SE` strings in `stores/__init__.py` and `notifier.py` | accept | Per CONTEXT.md "Out of Scope" / I18N-01 deferred — preserved verbatim by design. No PII in store hints or email templates. Severity: info. |
| T-01-07 | Denial of Service | parser.py LiteLLM endpoint (verbatim port) | accept | URL points at `http://litellm:4000` — unreachable in this repo (no LiteLLM service). Phase 2 swaps for OpenRouter. Until then, parser code is unreachable at runtime; tests mock the HTTP layer. Severity: info (deferred). |

No high-severity threats in this plan.
</threat_model>

<verification>
After both tasks complete:

```bash
# Imports clean — zero source-repo paths leak through
test "$(grep -rn 'from modules.price_tracker' src/domain/ | wc -l)" = "0" || (echo FAIL: stale modules.price_tracker import; exit 1)
test "$(grep -rn 'from core\.' src/domain/ | wc -l)" = "0" || (echo FAIL: stale core. import; exit 1)
# Column rename complete
test "$(grep -rn context_id src/domain/ | wc -l)" = "0" || (echo FAIL: stale context_id; exit 1)
# Table prefix dropped
test "$(grep -c price_tracker_ src/domain/models.py)" = "0" || (echo FAIL: stale price_tracker_ prefix; exit 1)
# All five public symbols importable through the package __init__
poetry run python -c "from domain import PriceTrackerService, PriceParser, PriceCheckScheduler, PriceNotifier, PriceExtractionResult; print('domain package OK')"
# LOC parity check on the four pure-copy files
test "$(wc -l < src/domain/result.py)" = "19"
test "$(wc -l < src/domain/extractors/__init__.py)" = "1"
test "$(wc -l < src/domain/extractors/willys_api.py)" = "101"
test "$(wc -l < src/domain/stores/__init__.py)" = "66"
# Mapping table present
grep -q "Verbatim Port Mapping" .planning/phases/01-skeleton-domain-copy/01-02-SUMMARY.md
echo "domain port verified."
```
</verification>

<success_criteria>
- 11 domain files exist under `src/domain/` (REQ DOMAIN-01, DOMAIN-02, DOMAIN-03)
- Zero `from modules.price_tracker` and zero `from core.` imports remain
- Zero `context_id` references in any ported file
- Zero `price_tracker_` table-name references in `models.py`
- Five `__tablename__` values are `stores`, `products`, `product_stores`, `price_points`, `watches`
- All five domain public symbols importable via `from domain import ...`
- Pure-copy files (`result.py`, `extractors/__init__.py`, `extractors/willys_api.py`, `stores/__init__.py`) match source LOC exactly
- `01-02-SUMMARY.md` includes the verbatim port mapping table with all 11 rows (D-16)
</success_criteria>

<output>
After completion, create `.planning/phases/01-skeleton-domain-copy/01-02-SUMMARY.md` containing:
- The Verbatim Port Mapping Table (all 11 rows, 2034 LOC total)
- Count of `context_id` -> `tenant_id` symbol rewrites in service.py and scheduler.py (with the specific lines)
- Confirmation that LITELLM_API_BASE constant + usage preserved (Phase 2 swap)
- Any source-line surprises encountered (e.g., extra `context_id` references not on the original list)
</output>
