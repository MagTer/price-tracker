# Session state: aligning price-tracker with `shared/core.md`

**Status 2026-10-29: PLAN, unsigned. Nothing below is built.** Origin: operator, 2026-10-29:
*"Skriv en plan i respektive repo för att aligna till agent-config's sätt att hantera
sessionen"*. The rule is agent-config `shared/core.md`, "Session state" (agent-config
`b3f6829`); its measurements, pilot log and rejected alternatives are in agent-config
README, "Session state is one overwritten, gitignored file, decided 2026-10-29".

## What the rule asks

Where a checkout stands lives in `.agent-state.md` at the repository root, gitignored,
rewritten and never appended, at most 40 lines, carrying `head` and the client that wrote
it; read first, and the commits since `head` read after it. A project's own instruction file
outranks `core.md`, so anything here that contradicts the rule wins until this plan changes it.

## What is here today, read 2026-10-29

| Reading | Command | Value |
|---|---|---|
| A position file | `ls -a .agent-state.md; grep -c agent-state .gitignore` | none; 0 |
| `.planning/STATE.md` | `wc -lw .planning/STATE.md`; `git log -1 --format=%ci -- .planning/STATE.md` | 212 lines, 5 079 words; last touched 2026-09-06 |
| What STATE.md is | its own header | retired 2026-07-26: the decision log D-19..D-34 and an open-items list, "not a live pipeline" |
| What the vault says about this repo | `python3 -I ~/dev/home-server/scripts/repo-digest.py render ~/dev/price-tracker` | **"Last activity: v0.59.0 …"** — read from STATE.md's `last_activity_desc`, while `git tag --sort=v:refname \| tail -1` is v0.67.0 |
| Session-position text in CLAUDE.md | `grep -n '^\*\*Status' CLAUDE.md` | one paragraph, "Status (2026-08-13)", with a test count (857) beside the command that reads the real one |
| A handoff file | `head -12 .planning/OPUS-HANDOFF.md` | July 2026, tells a session to "log finished tasks in STATE.md" and cites "113 green" |

So this repo has **no competing position mechanism**: sessions derive their position from
`git log` and the deploy-state command in CLAUDE.md. What it has is three places that *look*
like position and are stale, and one of them is published nightly to the vault.

## Decisions for the operator

**P-D1 — what STATE.md's frontmatter says.** The nightly repo digest (home-server
`scripts/repo-digest.py`) reads `last_activity_desc` and `stopped_at` from
`.planning/STATE.md` in every repo and writes them to the vault as "Current state". Here they
are frozen at 2026-09-06, so the vault reports v0.59.0 as the latest work, and an agent asking
the vault gets that as fact. Options: (a) replace both fields with one dated pointer, double-quoted (the digest reads nothing else) ("no
position is kept here: the working position is `.agent-state.md`, local; history is `git
log`") — the digest then prints the pointer, which is true; (b) delete the two fields — the
digest then says the file "carries no `last_activity_desc` or `stopped_at`", also true, less
helpful; (c) leave them — the vault keeps saying v0.59.0. **Recommend (a).** It changes only
two frontmatter lines and breaks nothing that reads the decision log. This interacts with
home-server's plan, D3 (what the digest should read at all); (a) is right under any answer
there.

**P-D2 — the "Status (2026-08-13)" paragraph in CLAUDE.md.** It is the one piece of
session-like state CLAUDE.md still carries, and it holds a moving value (the test count) that
`core.md` says never to write down. Options: (a) keep the paragraph's durable half (the
product is live; the integration-tier recipe; the CI command that reads the count) and drop
the count and the date-stamped status claim; (b) leave it. **Recommend (a)**; nothing reads
the number but a session, and a session told "857" will repeat it.

**P-D3 — `OPUS-HANDOFF.md`.** A July handoff whose instructions (log to STATE.md) contradict
the file it points at. Options: delete it (it stays readable by `git show`), or leave it with
its own staleness banner, which it already has. **Recommend delete**, naming the sha in the
commit body.

Out of scope, said so it is not mistaken for part of this: the "Since 04.1" release index in
CLAUDE.md. It is an index of where rules live, not session position; whether it is too long is
a pruning decision of its own.

## Units

### U1 — the ignore line
- **Goal.** `.agent-state.md` is ignored here before any session writes one.
- **Files.** `.gitignore` (the line, with a one-line comment naming `shared/core.md`).
- **Acceptance.** `git check-ignore .agent-state.md` prints the path.
- **Must not touch.** Anything else. (The rule converges without this unit — the first writer
  adds the line — so U1 is optional and costs nothing.)

### U2 — STATE.md stops claiming a position (P-D1)
- **Goal.** The vault digest prints a true sentence about where work stands.
- **Files.** `.planning/STATE.md` frontmatter: `stopped_at`, `last_activity_desc`,
  `last_updated`, `last_activity`. The body (decision log, open items) is untouched.
- **Acceptance.** The render command in the table above shows the pointer, not v0.59.0;
  `grep -n 'D-34' CLAUDE.md .planning/STATE.md` still resolves.
- **Must not touch.** The decision log entries; home-server's digest code.

### U3 — CLAUDE.md drops its moving status (P-D2)
- **Goal.** CLAUDE.md carries no test count and no dated "status" claim.
- **Files.** `CLAUDE.md`, the "Status" paragraph only.
- **Acceptance.** `grep -nE '857|776 passed' CLAUDE.md` finds nothing; the CI command that
  reads the count is still there.
- **Must not touch.** Architecture, Gotchas, the release index.

### U4 — the handoff goes (P-D3)
- **Files.** `.planning/OPUS-HANDOFF.md` deleted, its sha named in the commit body. The one
  other reference (`grep -rn OPUS-HANDOFF .` → STATE.md D-31, 2026-07-13) is a dated history
  entry and stays as written.

**Gates for every unit:** `poetry run ruff check src tests && poetry run ruff format --check
src tests && poetry run pytest -q`. They cannot see any of this (docs only) — the acceptance
lines above are the real checks. Docs-only, so no release.
