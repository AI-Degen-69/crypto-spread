# CONSTRAINTS.md — Issue #281: golden dataset from pristine days

Branch: `i281/golden-from-pristine` · Size: Standard · Type: Code + Docs

## Hard boundaries
1. **Zero regressions:** `python -m pytest tests/test_build_golden_dataset.py tests/test_verify_tick_data.py tests/test_backtest_index.py -q` green. Full suite stays with CI.
2. **Sources are sacred:** pristine and raw capture files are never modified, moved, or
   rewritten — golden assembly is copy-only, atomic, and idempotent.
3. **No silent inclusion:** a day that fails any §1.1 gate is excluded AND recorded with a
   reason in the manifest. Never mix in a failing day, never drop one silently.
4. **Determinism:** same inputs ⇒ byte-identical manifest (except the explicit
   `certified_utc` date, which is keyed by content hash of the inputs).
5. **No new dependencies.** stdlib + existing project modules only.
6. **Anti-cheat:** no skipping/disabling tests, no deleted assertions, no suppressed linters.
7. **Charter fidelity:** the manifest schema follows `docs/golden-tick-dataset.md` §3.1
   (day, verify_verdict, sha256, set totals, policy_version, certification date). Where this
   plan and the charter disagree, the charter wins.
8. **Scope lock:** no collector/verify-engine changes, no new capture runs, no dashboard
   changes, no bounds-gate re-litigation.
