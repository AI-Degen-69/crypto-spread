# CONSTRAINTS.md — Quality Guardrails for Issue #350

## Scope & Nature
- **Issue**: #350 `ops: read the #174 Phase 1 book_shadow numbers and record the go/no-go verdict`
- **Tier**: Tiny (Documentation / Operational record)
- **Target File**: `docs/issue-174-socket-book-disagreement.md`
- **External Action**: Comment on Issue #174 with final measurement numbers and NO-GO verdict.

## Guardrails
- **Zero code changes**: No modifications to live trading engine, collectors, or strategy files.
- **Ground truth only**: All numbers must match `run/ticks/manifest.json` (`comparisons=39414`, `divergent=11737`, `divergence_rate=0.2978`, `max_bb=0.32`, `max_ba=0.32`). No synthetic or guessed statistics.
- **Zero regressions**: No existing tests modified or removed.
- **Anti-cheat**: No test suppression or skipping.
- **No new dependencies**: Zero dependencies added.
