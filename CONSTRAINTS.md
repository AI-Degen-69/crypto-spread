# CONSTRAINTS — Issue #298: Rebuild run/ticks/pristine after the bounds_violation gate lands

Active working constraints for issue #298. Per-issue file; goes stale on merge.
(Archived #297 constraints removed post-merge; #297 shipped as PR #299.)

## Hard Gates (must hold before merge)

1. **Zero code changes to the extractor or gates.** This issue is an operational rebuild +
   findings record. `scripts/build_pristine_dataset.py`, gate thresholds, and all source
   modules stay byte-identical to `master` (post-#297).
2. **Source day files are read-only.** No modification or regeneration of any
   `run/ticks/ticks_*.jsonl[.gz]`. Proof: `totals.source_files` SHA-256 map in the new
   manifest must equal the pre-rebuild baseline map.
3. **Default thresholds only.** The rebuild runs with no gate-tuning flags, so the gate set
   matches the merged #297 behavior exactly.
4. **Self-certification must PASS.** Exit 0 and `output verify: PASS`; new manifest carries
   `output_verify.status == "PASS"` + the bounds-gate `policy_note` clause; independent
   `python -m scripts.verify_tick_data run/ticks/pristine` cross-check also PASS.
5. **Baseline preserved before overwrite.** `run/` is git-ignored; the current
   `run/ticks/pristine/pristine_manifest.json` (6,129 windows / 5,042 passed / 1,485,319
   ticks) is snapshotted to a scratch path **before** the rebuild starts.
6. **Committed artifacts are docs only.** Only `docs/issues/298-pristine-manifest-delta-findings.md`
   and `docs/measurements/issue-298-pristine-manifest-delta.json` (raw counts). No `run/`
   content is ever committed.
7. **Full-repo pytest sweep is CI-only** (repo policy). This issue changes no Python modules;
   the targeted gate `python -m pytest tests/test_build_pristine_dataset.py -q` must stay
   37/37 green (proves the branch didn't touch the extractor).

## Scope Guardrails

- IN: baseline snapshot, rebuild run, acceptance verification, delta computation, committed
  findings document, issue comment with the delta.
- OUT: changing gates/thresholds; dashboard visibility (#295 owns); touching
  `strategy/`, `server/`, `backtest/`; committing anything under `run/`.
- NOTICED-BUT-NOT-TOUCHING: anything spotted during the rebuild that is out of scope becomes
  a future issue candidate, recorded in the findings doc or plan — never a silent side change.

## Anti-cheat

- Delta numbers come from the two real manifests only — never from estimates or the 291
  baseline doc (that doc is a fallback reference, not a substitute when the on-disk manifest
  exists, and it does).
- Every dropped window must carry `bounds_violation` in the new `failing_gates` — if any
  drop fails this invariant, stop: the extractor changed or the matching is wrong.
