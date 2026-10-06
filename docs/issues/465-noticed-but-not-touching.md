# Noticed-but-not-touching — Issue #465 (Station III)

Candidates spotted while executing `tasks/plan.md` for #465. Only Station VI
(`vi-close-pipeline`) resolves rows here; other stations append `open` rows.

| ID | Candidate | Discovering station | Evidence | Status | Resolution |
|---|---|---|---|---|---|
| N1 | `assert_config_mirror` iterates the **module** `UNIVERSE` for its exit-threshold checks, not the scope's universe, so a `--run` whose universe differs from the frozen #146 triple would skip the exit-threshold mirror for its own slugs. Harmless today (all three recorded runs share the same universe) and out of #465's scope — the task was to parameterise the scope, not to re-shape the mirror. | III | `scripts/replay_shadow_check.py:240` (`for slug in UNIVERSE:`) vs `RunScope.universe` used by `load_scoped_snaps`/`select_groups` | open | — |
