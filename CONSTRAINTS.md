# CONSTRAINTS — Issue #287: pipeline leftovers hygiene rule

## Scope guard
- Docs-only. No code behavior changes anywhere.
- One rule addition in `docs/git-workflow.md` (new §7 or §5 extension).
- Master is already clean (chore `adc806a`); no cleanup commit needed unless
  new dirt appears during this work.

## Measurable boundaries
- The rule names: who commits showcase pages (creating station, same PR),
  who removes per-issue scratch (Station VI, before merge), and the
  clean-tree check (`git status --porcelain` empty) as merge precondition.
- Rule text ≤ 10 lines, same tone as surrounding sections.

## Anti-cheat
- No test changes of any kind.

## Zero regressions
- Targeted gate: none required (docs-only); `git status --porcelain` clean
  is the verification. Full suite stays with CI.
