# CONSTRAINTS — Issue #279: auto-pick healthiest tick file as the default backtest dataset

## Scope guard
- Files touched: `server/osc_dash.py`, `tests/test_osc_dash_integration.py`,
  `tests/test_theme_tokens.py`, plus the three per-issue working files (`tasks/plan.md`,
  `tasks/todo.md`, this file + `SPEC.md`) — plus the already-committed
  `docs/issues/288-presentation-hygiene-rule.html` (#288 artifact filed by the
  post-merge sweep, disclosed in the PR). Nothing else.
- Additive API only: no existing `/api/ticks/manifest` field renamed, re-typed, or removed;
  `preferred_file`/`is_preferred` are new keys.
- Out of scope (hard): `/api/ticks/verify`, `scripts/verify_tick_data.py` logic or thresholds,
  backtest engine math, other dashboard tabs, `strategy/*`, forced re-verifies,
  `requirements.txt`.

## Zero regressions
- Targeted gate: `python -m pytest tests/test_osc_dash_integration.py tests/test_theme_tokens.py -q`
  passes — both before the work starts (baseline) and at closeout.
- No existing test is modified except extending the two files named in the issue
  (`test_osc_dash_integration.py` manifest/verify endpoint tests, `test_theme_tokens.py`
  dropdown assertions).
- Full-repo sweep stays with CI on push (repo policy — never run locally).

## Anti-cheat
- No skipping, disabling, deleting, or weakening of any test or assertion.
- No suppression of linters or type checks; no `# noqa` introduced.
- No new external dependency without explicit operator approval (`requirements.txt` stays at 5).

## Determinism & safety of the ranking
- `pick_preferred()` is pure: no I/O, no globals, no wall-clock reads — output is a function
  of its argument list only.
- Eligibility compares the exact strings the verify engine emits (`"PASS"`,
  `"COMPLETE CAPTURE"`) — no fuzzy matching, no case folding.
- Exactly one `is_preferred: true` across all files whenever a winner exists; zero when not.

## UI guardrails
- Badge and ★ reuse existing theme tokens (`--gold`, panel/line variables) — no new CSS
  files, no inline hex literals for themed colors.
- The manual-selection persistence path (`currentVal` in `loadManifest()`) is not weakened;
  the pre-select only fires when nothing is stored yet.
