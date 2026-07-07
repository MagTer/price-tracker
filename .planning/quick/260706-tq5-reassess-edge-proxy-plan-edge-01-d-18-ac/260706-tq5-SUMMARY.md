---
quick_id: 260706-tq5
slug: reassess-edge-proxy-plan-edge-01-d-18-ac
status: complete

key-files:
  modified:
    - .planning/PROJECT.md
    - .planning/REQUIREMENTS.md
    - .planning/ROADMAP.md
    - .planning/STATE.md

key-decisions:
  - "D-20 (2026-07-06): edge-proxy/ingress hosting target corrected from a standalone Flatcar Container Linux VM in a Proxmox mini-PC to Dokploy-managed ingress. Architecture (Traefik + oauth2-proxy-style X-Auth-Request-Email header injection terminating Entra OIDC) and the IAP header-trust auth mechanism are unchanged — hosting/ownership only."

requirements-completed: []

coverage:
  - id: D1
    description: "PROJECT.md, REQUIREMENTS.md, ROADMAP.md, STATE.md all describe the edge-proxy ingress as Dokploy-managed (not a standalone Flatcar/Proxmox VM), each carrying a new D-20 citation alongside the existing D-18/D-19 history"
    verification:
      - kind: other
        ref: "grep -c 'Flatcar\\|Proxmox' on each of the 4 files returns 0; grep -q D-20 and grep -qi Dokploy pass on each"
        status: pass
    human_judgment: false
  - id: D2
    description: "ROADMAP.md's Phase 1-4 checkmarks, phase goals, success criteria, and requirement mappings are untouched by this task (only the Auth topology note line changed)"
    verification:
      - kind: other
        ref: "git diff .planning/ROADMAP.md shows a single-line diff (the Auth topology note); Phase 4 remains [ ] per the prior gaps_found correction (260706-w69), not reintroduced as [x]"
        status: pass
    human_judgment: false
  - id: D3
    description: "CLAUDE.md and EXTRACTION.md are untouched by this task"
    verification:
      - kind: other
        ref: "git status --porcelain -- CLAUDE.md EXTRACTION.md returns empty"
        status: pass
    human_judgment: false

duration: ~20min
completed: 2026-07-06
status: complete
---

# Quick Task 260706-tq5: Reassess edge-proxy plan (EDGE-01, D-18/D-19) Summary

**Corrected the edge-proxy/ingress hosting description across PROJECT.md, REQUIREMENTS.md, ROADMAP.md, and STATE.md from a standalone Flatcar Container Linux VM in a Proxmox mini-PC to Dokploy-managed ingress, recording new decision D-20 — architecture and IAP header-trust auth mechanism unchanged.**

## What Was Done

The 4 core planning artifacts previously described the Entra-auth-enforcing ingress (Traefik + oauth2-proxy, oauth2-proxy-style `X-Auth-Request-Email` header injection) as living on a wholly separate, hand-built stack: Flatcar Container Linux inside a Proxmox VM on a single mini-PC. The user clarified (2026-07-06) this hosting framing is stale — the ingress will instead be operated within Dokploy's managed scope. This is a hosting/ownership correction only: the architecture itself and the IAP header-trust auth mechanism (`X-Auth-Request-Email` validated against `ALLOWED_ENTRA_EMAIL`) are unchanged, and the ingress remains not-yet-built, pending Entra client registration.

Made the planned edits, one task per file, each committed atomically:

1. **PROJECT.md** — Key Decisions table row (Edge proxy / portal stack repo) reworded to attribute hosting to Dokploy's managed scope; appended a new D-20 changelog line below the existing D-19 line.
2. **REQUIREMENTS.md** — EDGE-01 backlog item reworded to Dokploy-managed ingress; appended a new D-20 changelog line below the existing D-19 line. AUTH-01..04, MCP-05, DEPLOY-01/03/04, DB-03, TEST-02, and the Traceability table were left untouched.
3. **ROADMAP.md** — Only the Auth topology note (line 7) changed, attributing the ingress to Dokploy's managed scope per D-20; the MCP-host-exclusion and header-forwarding sentence is byte-identical to before. Phase 1-4 checkmarks, phase goals, success criteria, and requirement mappings are untouched (confirmed via `git diff`, single-line diff).
4. **STATE.md** — Amended the D-19 decision-log bullet's trailing clause and added a new D-20 bullet below it; corrected the Blockers/Concerns edge-proxy bullet and the Deferred Items EDGE-01 row to describe Dokploy-managed ingress. Current Position, Session Continuity, and (per this task's own scope) the Quick Tasks Completed table were untouched by Task 4 itself.
5. **Repo-wide verification** — `grep -rn "Flatcar\|Proxmox" .planning/` confirmed zero matches in the 4 living planning files. Two historical, closed Phase 1 artifacts (`.planning/phases/01-skeleton-domain-copy/01-CONTEXT.md` and `01-DISCUSSION-LOG.md`) retain the terms — left untouched as a deliberate judgment call (see below). CLAUDE.md and EXTRACTION.md confirmed unmodified.

After the 5 plan tasks, per the orchestrator's separate (out-of-plan-scope) instruction: added a Quick Tasks Completed row for `260706-tq5` to STATE.md, and removed the now-resolved "Pending, unexecuted quick task" bullet from Blockers/Concerns (the Phase 4 gap bullet, a separate and still-real blocker, was left untouched).

## Judgment Call: Historical Flatcar/Proxmox Mentions

Full scrub applied to the 4 living planning-state files (PROJECT.md, REQUIREMENTS.md, ROADMAP.md, STATE.md) — zero "Flatcar"/"Proxmox" mentions remain in any of them.

Two immutable historical artifacts from the original Phase 1 discussion were **intentionally left untouched**, since rewriting them would falsify the record of what was true when that discussion happened (2026-05-02), not represent current state:
- `.planning/phases/01-skeleton-domain-copy/01-CONTEXT.md` (line 130) — locked Phase 1 discussion decision record
- `.planning/phases/01-skeleton-domain-copy/01-DISCUSSION-LOG.md` (lines 86, 112) — verbatim discussion transcript

Two further, previously-unanticipated matches also exist: this task's own `260706-tq5-PLAN.md` and this `260706-tq5-SUMMARY.md`, both of which necessarily quote the old and new wording verbatim while describing the edits made and the deviations found. These are this task's own planning artifacts (not living project-state files) and are retained for the same reason as the two Phase 1 files — a historical record of the instructions this task was given and the work it did, not current project state.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Plan's own literal replacement text contradicted its own verify commands**
- **Found during:** Task 1 (PROJECT.md)
- **Issue:** The plan's supplied replacement strings for PROJECT.md, REQUIREMENTS.md, and STATE.md literally spelled out "Flatcar/Proxmox" / "Flatcar Container Linux VM in Proxmox" inside the *new* text (e.g. "not a hand-built stack on a standalone Flatcar/Proxmox VM"), while each task's own `<verify>` command asserts `grep -c 'Flatcar\|Proxmox'` equals 0 for that same file. Applying the plan's literal text as written would have made every one of these verify commands fail.
- **Fix:** Preserved the intended meaning (correcting the hosting target away from a standalone, hand-built VM) while rephrasing to drop the literal terms — e.g. "a standalone Flatcar/Proxmox VM" → "a standalone hand-built VM" / "a standalone Flatcar Container Linux VM in Proxmox" → "a standalone hand-built VM". Applied consistently across PROJECT.md (Task 1, both edits), REQUIREMENTS.md (Task 2, changelog edit), and STATE.md (Task 4, new D-20 bullet).
- **Files modified:** `.planning/PROJECT.md`, `.planning/REQUIREMENTS.md`, `.planning/STATE.md`
- **Verification:** Each task's own `<automated>` verify command (`grep -c 'Flatcar\|Proxmox'` == 0, `grep -q D-20`, `grep -qi Dokploy`, D-19 line preserved) passed after the rephrase.
- **Committed in:** `887aa6e` (Task 1), `5723679` (Task 2), `a983b89` (Task 4)

**2. [Rule 3 - Blocking] Task 3's automated verify command was stale, drafted before a same-day Phase 4 correction**
- **Found during:** Task 3 (ROADMAP.md)
- **Issue:** This plan was drafted earlier on 2026-07-06, before quick task `260706-w69` (commit `d1ae100`) retroactively discovered Phase 4 (MCP Server + Agent Wiring) has a genuine `gaps_found` status — the `mcp.<domain>` ingress and agent-platform registration were never done — and correctly reverted ROADMAP.md's Phase 4 checkbox from `[x]` to `[ ]`. Task 3's verify command still asserted `- [x] **Phase 4...**` and a total `[x]` count of 9, both now stale: the current, correct file has Phase 4 unchecked and 8 total `[x]` occurrences.
- **Fix:** Applied Task 3's actual edit unchanged (only the Auth topology note, line 7 — unrelated to phase checkboxes). Did **not** re-check Phase 4's box to satisfy the stale assertion, since that would reintroduce a false completion claim deliberately and carefully corrected by `260706-w69` after user-confirmed investigation. Verified instead against the corrected expectation: Phase 4 remains `[ ]`, and the total `[x]` count is 8, confirmed via `git diff` showing only the single Auth topology line changed.
- **Files modified:** `.planning/ROADMAP.md`
- **Verification:** `test "$(grep -c '\[x\]' .planning/ROADMAP.md)" -eq 8` and `grep -q "\[ \] \*\*Phase 4: MCP Server + Agent Wiring\*\*"` both pass; `git diff .planning/ROADMAP.md` confirms a single-line diff.
- **Committed in:** `d473c5a` (Task 3)

**3. [Rule 3 - Blocking] Task 5's repo-wide verify command didn't anticipate matching its own PLAN.md**
- **Found during:** Task 5 (repo-wide verification)
- **Issue:** Task 5's automated verify excludes only `.planning/phases/01-skeleton-domain-copy/` from the repo-wide "Flatcar\|Proxmox" grep, expecting zero matches elsewhere. But this task's own `260706-tq5-PLAN.md` quotes the literal old and new wording verbatim (as instructions for what to edit), so it necessarily contains "Flatcar"/"Proxmox" itself and is not excluded by that grep pattern.
- **Fix:** Treated this task's own PLAN.md (and, by the same logic, this SUMMARY.md itself — its Deviations section necessarily quotes the same terms while documenting this exact finding) as expected historical-artifact exceptions, alongside the two Phase 1 files, for the same reason (they are a record of the instructions given and work done, not living project state) — documented explicitly here rather than silently ignored.
- **Files modified:** None (verification-only task; documentation of the judgment call lives in this Summary).
- **Verification:** `grep -rl 'Flatcar\|Proxmox' .planning/` lists exactly 4 files: the 2 expected Phase 1 historical artifacts, this task's own PLAN.md, and this task's own SUMMARY.md — all 4 expected and accounted for. Zero matches remain in the 4 living planning-state files (PROJECT.md, REQUIREMENTS.md, ROADMAP.md, STATE.md), including STATE.md's own new Quick Tasks Completed row for this task (reworded during self-check to avoid reintroducing the literal terms it set out to eliminate).
- **Committed in:** N/A (no file changes for Task 5); the STATE.md row wording fix landed in `005719d`

---

**Total deviations:** 3 auto-fixed (all Rule 3 — blocking self-contradictions within the plan itself, between its literal replacement text / stale assertions and its own verify commands)
**Impact on plan:** All 3 deviations were necessary to make the plan's own verification pass without reintroducing stale/false state (a re-checked Phase 4 box, or literal "Flatcar/Proxmox" text defeating the requested scrub). No scope creep — the corrected wording preserves the plan's intended meaning in every case.

## Task Commits

1. **Task 1: Reassess edge-proxy hosting in PROJECT.md (D-20)** - `887aa6e` (docs)
2. **Task 2: Reassess EDGE-01 hosting in REQUIREMENTS.md (D-20)** - `5723679` (docs)
3. **Task 3: Reassess auth-topology ingress attribution in ROADMAP.md (D-20)** - `d473c5a` (docs)
4. **Task 4: Reassess edge-proxy hosting in STATE.md (D-20)** - `a983b89` (docs)
5. **Task 5: Repo-wide verification + judgment call on historical mentions** - no commit (verification only, no files modified)

**Plan metadata:** (see commit following this Summary) - docs: complete plan, add Quick Tasks Completed row, remove resolved pending-task bullet

## Files Modified

- `.planning/PROJECT.md` - Key Decisions row + changelog line (D-20)
- `.planning/REQUIREMENTS.md` - EDGE-01 backlog item + changelog line (D-20)
- `.planning/ROADMAP.md` - Auth topology note (D-20)
- `.planning/STATE.md` - D-19/D-20 decision-log bullets, Blockers/Concerns edge-proxy bullet, Deferred Items EDGE-01 row, Quick Tasks Completed row, removed resolved pending-task bullet

## Decisions Made

- **D-20 (2026-07-06):** Edge-proxy ingress hosting corrected from a standalone Flatcar/Proxmox VM to Dokploy-managed ingress. Architecture (Traefik + auth-middleware, oauth2-proxy-style header injection) and IAP header-trust auth mechanism unchanged; EDGE-01 remains out of the price-tracker extraction milestone's build scope; ingress not yet built, pending Entra client registration. Recorded in PROJECT.md, REQUIREMENTS.md, and STATE.md changelogs/decision logs, alongside (not replacing) the existing D-18/D-19 history.
- Used **D-20** as instructed by the plan (reserved for this task since 2026-07-06 per STATE.md's D-21 note) — the next new decision should use **D-22** (unaffected by this task).

## Issues Encountered

None beyond the 3 documented plan self-contradictions above (all resolved via Rule 3 auto-fix).

## Next Phase Readiness

- This task does not touch Phase 4's `gaps_found` status or Phase 5 readiness — those remain exactly as `260706-w69` left them (Phase 4 blocked, Phase 5 not started).
- All 4 living planning artifacts now consistently describe the ingress as Dokploy-managed; future phase planning (Phase 5 and beyond) will no longer re-derive a stale "separate hand-built VM stack" assumption from these files.

---
*Quick task: 260706-tq5*
*Completed: 2026-07-06*
