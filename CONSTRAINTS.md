# CONSTRAINTS — Issue #294: Rank the least-bad tick file as preferred when no file fully qualifies

## Scope guard
- Files touched: `server/osc_dash.py`, `tests/test_osc_dash_integration.py`,
  `tests/test_theme_tokens.py`, plus the three per-issue working files (`tasks/plan.md`,
  `tasks/todo.md`, this file + `SPEC.md`). Nothing else.
- Additive API only: `preferred_tier` is a new key. Existing `preferred_file` and
  `is_preferred` semantics widened (may now be non-null when only tier 2 qualifies) — but
  no field renamed, re-typed, or removed.
- Out of scope (hard): `/api/ticks/verify`, `scripts/verify_tick_data.py` logic or thresholds,
  backtest engine math, other dashboard tabs, `strategy/*`, forced re-verifies,
  `requirements.txt`, pristine files (#295).

## Zero regressions
- Targeted gate: `python -m pytest tests/test_osc_dash_integration.py tests/test_theme_tokens.py -q`
  passes — both before the work starts (baseline) and at closeout.
- Existing tier-1 tests (`test_manifest_preferred_file_picks_healthy_winner`,
  `test_pick_preferred_pure_ranking`, etc.) must keep passing unchanged — tier-1
  behavior is preserved, not replaced.
- Full-repo sweep stays with CI on push (repo policy — never run locally).

## Anti-cheat
- No skipping, disabling, deleting, or weakening of any test or assertion.
- No suppression of linters or type checks; no `# noqa` introduced.
- No new external dependency without explicit operator approval (`requirements.txt` stays at 5).

## Determinism & safety of the ranking
- `pick_preferred()` remains pure: no I/O, no globals, no wall-clock reads.
- Tier 1 still compares exact strings (`"PASS"`, `"COMPLETE CAPTURE"`) — no fuzzy matching.
- Tier 2 uses a defined total order (see SPEC.md) — deterministic, no ambiguity.
- Exactly one `is_preferred: true` across all files whenever a winner exists; zero when not.

## UI guardrails
- Badge and ★ reuse existing theme tokens (`--gold`, panel/line variables) — no new CSS
  files, no inline hex literals for themed colors.
- Tier-1 badge says "★ Preferred"; tier-2 badge says "★ Best available" — the UI never
  lies about the data quality behind the star.
- Manual-selection persistence path is not weakened.
