"""One-off no-loss check for the information-layout move (docs/planning/INFORMATION-LAYOUT.md,
decision P-D3). Removed in the commit that retires .planning/, so nothing keeps reporting
afterwards — the operator declined a standing conformance checker on 2026-10-30.

Takes every bold lead and every ##/### heading of CLAUDE.md at the base commit and requires
the lead's first six words (whitespace collapsed, case-insensitive) to be found in AGENTS.md,
.claude/skills/*/SKILL.md or docs/ (docs/planning/ excluded: the plan quotes its subject).
A lead from the release index ("Since 04.1") may instead be found among the lines REMOVED by
the commit that retired the index (--index-removed-by REV, or --staged before committing).
Prints every lead it could not place and exits 1 if there is one.

Usage: python3 scripts/layout_check.py [--base SHA] [--index-removed-by REV | --staged]
"""

import argparse
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
# Heading of a GSD block that held only a "profile not yet configured" placeholder; it
# carried no fact, so it has no destination. Printed, never silently skipped.
RETIRED = {"Developer Profile": "empty GSD placeholder, no content"}
LEAD = re.compile(r"^\s*(?:[-*]|\d+\.)?\s*\*\*(.+?)\*\*")
HEADING = re.compile(r"^#{2,3} (.+)$")


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(REPO), *args], capture_output=True, text=True, check=True
    ).stdout


def norm(s: str) -> str:
    return " ".join(s.split()).lower()


def leads(text: str) -> list[tuple[int, str, bool]]:
    out, in_fence, in_index = [], False, False
    for n, line in enumerate(text.split("\n"), 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith("**Since 04.1"):
            in_index = True
        elif line.startswith("**The extraction is finished"):
            in_index = False
        m = HEADING.match(line) or LEAD.match(line)
        if m:
            out.append((n, m.group(1), in_index))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="88e99e9")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--index-removed-by")
    g.add_argument("--staged", action="store_true")
    a = ap.parse_args()

    base = git("show", f"{a.base}:CLAUDE.md")
    files = [REPO / "AGENTS.md", *sorted(REPO.glob(".claude/skills/*/SKILL.md"))]
    files += [p for p in sorted((REPO / "docs").rglob("*.md")) if "planning" not in p.parts]
    corpus = norm("\n".join(p.read_text() for p in files if p.exists()))

    removed = ""
    if a.index_removed_by or a.staged:
        diff = (
            git("diff", "--cached")
            if a.staged
            else git("show", "--format=", a.index_removed_by)
        )
        removed = norm("\n".join(x[1:] for x in diff.split("\n") if x.startswith("-")))

    found = leads(base)
    unplaced, retired, via_removal = [], [], 0
    for n, lead, in_index in found:
        key = " ".join(norm(lead).split()[:6])
        if lead in RETIRED:
            retired.append((n, lead))
        elif key in corpus:
            continue
        elif in_index and removed and key in removed:
            via_removal += 1
        else:
            unplaced.append((n, lead))

    print(f"base {a.base}: {len(found)} leads; searched {len(files)} files")
    if via_removal:
        print(f"{via_removal} release-index leads placed by the removing diff")
    for n, lead in retired:
        print(f"retired  line {n}: {lead} ({RETIRED[lead]})")
    for n, lead in unplaced:
        print(f"UNPLACED line {n}: {lead}")
    return 1 if unplaced else 0


if __name__ == "__main__":
    sys.exit(main())
