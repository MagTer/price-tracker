---
type: quick-summary
slug: 260906-d55-ica-nattproben-in-i-repot
date: 2026-09-06
status: complete
released: none (docs-only — does not earn a tag, per CLAUDE.md)
---

# Summary

Two lines in this repo pointed at evidence that lived on a machine the platform
documents as disposable, and as of 2026-09-06 that machine has a one-command rebuild.
The ICA night probe now lives in `docs/experiments/ica-night-probe/`.

## Why now, and what would have happened

The probe answered one binary question across the night of 2–3 August 2026: does ICA
flip prices in a midnight batch, or by hand in the morning? Answer: midnight —
Bregott Maxi 39,90 → 48,95 between Sun 22:30 and Mon 00:15, then still through 06:00.

That conclusion was already harvested into this repo on 2026-08-14, in two places:

- `CLAUDE.md`'s schedule bullet, with all three caveats intact
- `.planning/STATE.md`'s open item "ICA:s måndagsfönster"

**Both referenced the raw data by absolute path** (`~/ica-natprobe/results.jsonl`) and
this repo held no copy. The home-server repo gained `make rebuild-dev` on 2026-09-06,
which replaces the dev VM's 50 GB disk from the image. STATE.md said the data would be
"städat när frågan är avgjord" — but the window question is still OPEN, so a rebuild
would have settled it by accident and left two dangling pointers in a repo that claims
the evidence exists.

The cost of preventing that is 28 KB.

## What moved

Five files, byte-identical on copy (verified with `cmp`), with two deliberate edits
afterwards — both because this is versioned source now rather than a directory on one
machine:

- `probe.py` had `sys.path.insert(0, "/home/magnus/dev/price-tracker/src")`. It now
  resolves `src` from its own location. The stated way forward for this question is
  "re-run the probe wider", and that must not require editing the file first. Verified
  the resolution lands on this repo's `src/` and finds `domain/extractors/jsonld.py`.
- The probe's own README said raw data is kept "utanför repot ... städas när frågan är
  avgjord". Corrected in place, not appended: the data is in the repo, and the reason it
  is kept is that the question is open and the next step needs it.

## Checked before committing

- `results.jsonl` fields are `run, at_utc, label, status, price, offer, offer_details` —
  no URLs, cookies, fingerprints or IPs. The URLs in `probe.py` are public ICA product
  pages with public store ids, of the same kind this app already tracks.
- No `~/ica-natprobe` reference remains anywhere in `CLAUDE.md`, `.planning/STATE.md` or
  `docs/`.
- `docs/experiments/` is a new directory. `docs/spikes/` was considered and rejected: it
  holds throwaway `.py` explorations, and this is a completed measurement with a result
  and an open decision hanging off it.

## Not done, deliberately

- **The ICA window is not moved.** That decision remains explicitly untaken, with its
  three caveats: one of four products moved (n=1 week), the move observed was a campaign
  ENDING (price up), which does not prove a new campaign appears at the same moment, and
  one 03:00 run returned 404 on a product that answered fine at 06:00.
- `~/ica-natprobe/` on the dev VM is **not deleted** — it stays until the operator has
  confirmed the copy.

## Verification

`ruff check` / `ruff format --check` / `pytest -q` are a regression check here, not proof:
nothing under `src/` or `tests/` was touched. The change is proved by `cmp` on the five
copied files, the path resolution check, and the absence of any remaining absolute
pointer.
