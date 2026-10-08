# CONSTRAINTS.md — Issue #467 (per-issue working file, pruned at closeout)

## Scope fence
- IN: `assert_config_mirror` signature + per-slug loop (`scripts/replay_shadow_check.py:216-247`), the four-leg driver call (`:419`), tests in `tests/test_replay_shadow_check.py`. Nothing else.
- OUT: `build_params` (frozen thresholds stay), threshold values (still 0.05 everywhere), any other mirrored knob, `docs/issues/465-noticed-but-not-touching.md` (N1 already records resolution #467; the ledger has no `fixed` status).

## Zero regressions
- Existing 3-arg and 4-arg positional calls keep working (new param is last, defaulted).
- Bare (no `--run`) #146 path byte-identical: `universe=None` falls back to module `UNIVERSE`.
- `python -m pytest tests/test_replay_shadow_check.py -q` must pass.

## Correctness
- Explicit `is None` test for the default — never truthiness (`()` means "check nothing", not "not given").
- Error text `exit mirror gap: <slug>` unchanged; expected value 0.05 unchanged.

## Anti-cheat
- No skipped/disabled tests, no weakened assertions, no new dependencies.
- If a check must change to match the requested behavior, explain why in `tasks/plan.md`.
