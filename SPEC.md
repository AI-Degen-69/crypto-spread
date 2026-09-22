# SPEC — Issue #290: pristine-window dataset extractor

## Goal
Extract the value already on disk: six capture days (2026-09-13 … 2026-09-21, ~1.87M lines,
6,129 windows) fail the golden charter's §1.2 day-level gates (boundary-round bursts put the
sampling-gap rate at ~0.2–0.4 per day), yet 87% of 5m and 67% of 15m windows are individually
pristine. Build `scripts/build_pristine_dataset.py`: verdict every captured window against
strict per-window continuity gates and emit a derived, quality-certified tick dataset of only
the pristine windows. Measured per-pair pristine counts (~799–802 per 5m market, ~208–209 per
15m market) are far above the golden target of ≥50 windows per pair.

## Acceptance criteria
1. `python -m scripts.build_pristine_dataset run/ticks --out run/ticks/pristine` writes
   per-start-day tick files + `pristine_manifest.json`; every source file untouched
   (hashes unchanged before/after).
2. Re-verifying the output with `scripts.verify_tick_data` reports zero late starts,
   early cutoffs, sampling gaps, time reversals, and collector errors.
3. The manifest records per-pair pristine counts and each window's verdict with its source
   file and sha256; a second run over the same inputs produces byte-identical output.
4. `python -m pytest tests/test_build_pristine_dataset.py -q` passes.

## Gates per window (verify_tick_data defaults; CLI-overridable)
- Late start ≤5s after window open · early cutoff ≤5s before window close.
- Zero sampling gaps >6s · zero time reversals · zero collector-error ticks (`err` field).
- Snap-density floor: ≥1 snap per 3s of window duration — catches a window full of 5.9s
  gaps that technically passes the >6s rule (`tick_count ≥ ceil(duration / 3.0)`).

## Output contract
- Whole-window output: **all** tick lines of a surviving window, written to
  `run/ticks/pristine/ticks_<start-day>.jsonl`, keyed to the window's **start** day. The
  collector splits midnight-spanning windows across two day files; the derived set must not,
  or the verifier would flag fake early cutoffs. Windows are merged across source files by
  cid before gating.
- `pristine_manifest.json` (written last, atomically): per-window verdict (pass/fail +
  failing gates, source file + sha256), per-pair pristine counts, gates block (thresholds),
  embedded `output_verify` verdict of the freshly written output, verify policy version.
- Byte-stable: no wall-clock fields anywhere; deterministic ordering of sources, windows,
  and counts. Sources never modified; re-run is idempotent.

## Edge cases
- Midnight-spanning window: parts from two day files merge into one stream keyed to start day.
- A window split across days must not appear as two partial verdicts — one cid, one verdict.
- Output dir refuse-overlap: `out == ticks_dir` → exit 2 before any write.
- Empty/absent day files and unparseable rows: skipped the way `iter_ticks` skips them;
  a window with zero valid ticks is a failing verdict (`no ticks in window`), never a pass.
- Whole-window grouping must preserve each window's original line order.

## Explicit out of scope
Modifying any existing day file; collector changes (PR #289 territory); promoting output into
`run/ticks/golden/` or amending the golden charter (separate decision); backtest engine changes.
