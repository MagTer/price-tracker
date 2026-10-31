# Information layout: aligning price-tracker with the shared rule

**Status 2026-10-31: PLAN, every decision taken (P-D1, P-D2, P-D3). Nothing below is built; it runs after agent-config's layout U1.** Origin: operator, 2026-10-29
— *"Vi vill inte tappa information, men det ska hanteras enligt gemensamma rutiner och inte
bloat'a sessionen. Repots readme i rooten är främst för människor, agents.md är
projektspecifika tillägg. Vissa saker kanske ska bli projekt-lokala skills."* The rule is
agent-config `docs/planning/2026-10-29-project-information-layout.md` (U1 there, unsigned) plus
`shared/core.md` "Session state" (agent-config `b3f6829`). **This plan runs only after that
U1 is signed**, so the layout it moves into is the one every repo uses.

This replaces the first draft of 2026-10-29 (session state only, at
`.planning/SESSION-STATE-ALIGNMENT.md`, commit `f73d5b1`): that draft kept GSD's `.planning/`
as a live place and treated `CLAUDE.md`'s size as out of scope; both were wrong against the
operator's direction.

## 1. What is here, read 2026-10-29 at `f73d5b1`

| Reading | Command | Value |
|---|---|---|
| Every-turn file | `wc -w CLAUDE.md` | **29 350 words** |
| by section | `awk '/^## /{…}'` word count per `##` | Architecture 20 665 · Project 6 271 · Gotchas 982 · Releasing 463 · Stack 378 · Conventions 278 · Prod logs 222 · two dead GSD blocks ("Project Skills", "Developer Profile", 52) |
| Of Project, the release index | "Since 04.1 — what changed" | ~45 bullets, one per release; its own header says every durable rule sits in Architecture or Gotchas and "Do not grow this list into a changelog" |
| Project skills | `ls .claude/skills` | none |
| GSD leftovers | `ls .planning` | `STATE.md` (decision log D-19..D-34 + open items, 5 079 words), PROJECT, REQUIREMENTS, ROADMAP, SEED-…, `OPUS-HANDOFF.md`, `phases/`, `quick/`, `config.json` |
| What the vault says | `python3 -I ~/dev/home-server/scripts/repo-digest.py render ~/dev/price-tracker` | "Last activity: v0.59.0 …" from STATE.md frontmatter, while `git tag --sort=v:refname \| tail -1` is v0.67.0 |
| Citations of `CLAUDE.md` | `git grep -c CLAUDE.md -- src tests docs alembic .github README.md` | 18 outside `.planning/` (code comments naming a bullet, e.g. "the transport bullet in CLAUDE.md", "Gotcha 4") |
| Agent memory that is really repo knowledge | `~/.claude/projects/-home-magnus-dev-price-tracker/memory/` | `render-check-admin-html` (how to drive the portal in headless Chromium) is knowledge about this code, and `core.md` says memory is a disposable cache. (`nextdns-blocks-store-cdns` is about the seat's DNS, not this repo, and stays) |
| Position file | `ls -a .agent-state.md` | none; `.gitignore` lacks the line |

## 2. Where each part goes

| Today | Goes to | Why there |
|---|---|---|
| Stack, Conventions, Releasing, Prod logs, Gotchas (each trap one or two lines), the deploy-state commands, the `src/` tree with its "THE definition" lines, data model, Auth's invariants | **`AGENTS.md`** — target under the proposal's 4 000-word hypothesis | binds every task |
| `CLAUDE.md` | the single line `@AGENTS.md` | the shared rule |
| Store knowledge: the extraction ladder, campaign = ordinarie + offer, Willys API, Rusta, Clas Ohlson, Lyko, ICA (+ online-only, veckoblad), JYSK, quick-add's identity ladder | skill **`store-extractors`** | read when adding or fixing a store |
| Getting through to stores: ledger, fetcher, transport, waf-token spike, breaker, scheduler self-defence, schedule, ICA midnight probe, leadership, `check_attempts` | skill **`store-fetching`** | read when a store walls us or the scheduler changes |
| The judgements: kr/enhet (04.1 shape), deals verdict + timing, Pristillfällen, statistics, validation, link health, manual price, offline paths, buy-list email, MCP surface | skill **`price-judgements`** | read when a number on any surface changes |
| The portal: fragments (Gotcha 2's recipe), hash routing, history modal, Att köpa, product list, Fel & luckor, Bevakningar, a11y, the refused design-handoff items, **the render-check recipe from agent memory** | skill **`portal-ui`** | read when `admin.html`/`shell.*` changes |
| The release index ("Since 04.1") | **leaves the tree**: `git log` is the changelog. Each bullet's facts that exist nowhere else move first (see U3) | `core.md`: "`git log` is the changelog" |
| `.planning/STATE.md` decision log D-19..D-34 + open items (MCP→Hermes registration) | **`docs/decisions.md`** | why, for people and agents; `AGENTS.md` points at it |
| Rest of `.planning/` (GSD artefacts, OPUS-HANDOFF) | **leaves the tree**; `docs/decisions.md` names the sha that holds it | history only, GSD uninstalled |
| What a person needs to run and deploy it | `README.md` (already 2 655 words; check it carries the deploy path) | for people |

## 3. Decisions

**P-D1 — the four skills' cut. DECIDED 2026-10-31 by the operator: the four.** Skills grouped by the task that needs them (above) versus
one skill per store and per page (finer, more files, more descriptions in every session's
skill list). **Recommend the four**: each description stays specific enough to load on the
right task, and the skill listing costs per session too.

**P-D2 — the release index's unique facts. DECIDED 2026-10-31 by the operator: (a), move the unique facts, then remove the index.** Most bullets point at an Architecture bullet
("→ X bullet"); some carry facts found nowhere else (e.g. v0.66.0's backup of deleted rows in
`~/price-tracker-backups/2026-10-19-llm-cleanup.json`, v0.62.0's 6-second duplicate). U3
moves those into the skill or `docs/decisions.md` row they belong to, then removes the index.
The alternative — keep the index in `docs/releases.md` — is a second changelog beside `git
log`. **Recommend removal after the move.**

**P-D3 — the no-loss check as a script. DECIDED 2026-10-31 by the operator: (a), the script, removed in U4.** The operator said "Låt bli kontrollverktyget" on
2026-10-30 about a checker that would report every repository's conformance from then on;
whether that covers this one-off migration check was not asked, and this plan's first draft
assumed it did not. (a) `scripts/layout_check.py` as below, used by U2–U4 and **deleted in
U4's commit**, so nothing is left that reports afterwards; (b) no script: each unit's commit
body lists, by hand, where every `**bold lead**` and heading of the base `CLAUDE.md` went.
**Recommend (a)**: 29 350 words is past what a hand list checks reliably, and "vi vill inte
tappa information" is the requirement the check exists for. If (b), U2–U4's acceptance lines
read "the commit body's list" wherever they say "the no-loss check".

Everything else follows from the shared rule and needs no separate decision.

## 4. Units

Gates for every unit: `poetry run ruff check src tests && poetry run ruff format --check src
tests && poetry run pytest -q`. They see the comment edits in U5 and nothing else; each unit's
own acceptance line is the real check. Docs only, so no release.

**No-loss check, used by U2–U4** (deterministic, written once in U2 as
`scripts/layout_check.py`, removed in U4; P-D3): take every `**bold lead**` and every `###`/`##` heading of the
`CLAUDE.md` at the base commit (`git show <base>:CLAUDE.md`), and require each lead's first
six words to be found in `AGENTS.md`, `.claude/skills/*/SKILL.md`, `docs/` or — for the
release index only — the commit that removed it. It prints the leads it could not place; a
non-empty list fails the unit.

### U1 — position and the ignore line
- **Files.** `.gitignore` (+ comment naming `core.md`).
- **Acceptance.** `git check-ignore .agent-state.md` prints the path.

### U2 — the skills and `AGENTS.md`
- **Files.** `AGENTS.md` (new, from CLAUDE.md's every-task parts); `CLAUDE.md` →
  `@AGENTS.md`; `.claude/skills/{store-extractors,store-fetching,price-judgements,portal-ui}/SKILL.md`;
  `scripts/layout_check.py`; at the other clients' project skill paths, the symlinks to
  `.claude/skills/` that agent-config's layout proposal U0 found necessary (none for
  OpenCode, which reads `.claude/skills/`).
- **Acceptance.** `wc -w AGENTS.md` reported in the commit body against 4 000; the no-loss
  check prints nothing for Architecture, Gotchas and Project-minus-index; each skill's
  `name:` matches its directory; a fresh session asked to change an ICA extractor loads
  `store-extractors` (Claude Code: the Skill call in its transcript; OpenCode: the `part`
  table query in agent-config README "Skill surface").
- **Must not touch.** `src/`, `tests/` (that is U5); `.planning/` (U4).

### U3 — the release index (P-D2)
- **Files.** `AGENTS.md`, the skills, `docs/decisions.md` (receive the unique facts).
- **Acceptance.** For each release bullet the commit body names where its unique facts went,
  or "none — all in <bullet>"; the no-loss check is clean.

### U4 — `.planning/` retires
- **Files.** `docs/decisions.md` (D-19..D-34 verbatim + open items); `.planning/` removed;
  `scripts/layout_check.py` removed (P-D3), after its last clean run;
  `docs/decisions.md` header names the removing commit's parent sha.
- **Acceptance.** `git grep -n 'D-2[0-9]\|D-3[0-4]'` resolves every citation to
  `docs/decisions.md`; the digest render prints "No `.planning/STATE.md` in this repo; the
  commits below are its state" — true, where today it prints v0.59.0.

### U5 — citations follow
- **Files.** the 18 comments in `src/`, `tests/`, `docs/`, `alembic/`, README that name a
  CLAUDE.md bullet → the skill or `AGENTS.md` section now holding it.
- **Acceptance.** `git grep -n 'CLAUDE.md' -- src tests docs alembic README.md` finds none
  that names a section; gates green.
- **Must not touch.** Behaviour. Comments only.

### U6 — agent memory
- **Files.** none in the repo (the recipe is in `portal-ui` since U2); the memory file
  that duplicates repo knowledge (`render-check-admin-html`) is deleted from agent memory, `MEMORY.md` updated.
- **Acceptance.** `ls ~/.claude/projects/-home-magnus-dev-price-tracker/memory/` holds only
  what is about the operator or the seat, not about this code.

**Must not touch, any unit:** home-server (the digest code), the deployed app.
