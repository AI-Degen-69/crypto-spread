# CONSTRAINTS.md — Issue #312 (golden dataset research cut)

## Hard boundaries
- **The golden dataset is read-only.** `run/ticks/golden/**` must be byte-identical
  before and after any build (sha256 per day file re-verified in-task). No code path
  may open a golden file for writing.
- **Determinism:** same inputs (golden dir, multiplier, seed) ⇒ byte-identical cut
  output and byte-identical manifest (except `ts` fields if any — the manifest records
  no wall-clock time that affects content).
- **The cut is derived, never canonical:** the manifest records source=golden with
  per-day source_sha256; final research claims re-run on the full golden set.

## Quality guardrails
- Zero regressions: `tests/test_verify_tick_data.py`, `tests/test_build_golden_dataset.py`
  must stay green; new behavior requires `tests/test_build_research_cut.py` coverage.
- The cut must independently pass `verify_tick_data` at RESEARCH_READY with the
  multiplier headroom (M=3: ≥1,500 windows, ≥150 per market-duration pair, ≥4 days,
  all 10 series). M=4 is infeasible: the scarcest golden pair (bnb 15m) holds 159
  windows < 200.
- Guardrail gate (replay parity + golden integrity) must pass inside the build; a
  failing gate fails the build loudly — no partial artifacts presented as complete.
- Anti-cheat: no skipping/disabling of existing tests, no assertion deletion, no
  lint suppression, no test weakening.
- No new external dependencies (stdlib only: json, random, hashlib, argparse, pathlib).

## Out of scope (do not touch)
- Charter gates, certification sequence, `READINESS_POLICIES` values.
- Sweep running (later issues), `.gz` output, index format.
