# SPEC — Issue #280: Charter the golden tick dataset for backtesting

## Goal
Author `docs/golden-tick-dataset.md`: a measurable definition of the single canonical tick
dataset for all backtesting, a plan to collect it, and a plan to certify it — then register
its agreed name in `AGENTS.md` + `docs/glossary.md`.

## Deliverable 1 — Definition of the golden dataset
- **Name (the one agreed term):** "the golden dataset" — the canonical backtest dataset;
  individual day files are "golden days"; the companion metadata file is "the golden manifest".
- **Location:** `run/ticks/golden/` (inside gitignored `run/` — it is data, not code; the
  manifest records provenance so the set can be rebuilt and re-certified).
- **Structure:** a set of verified per-day tick files (`ticks_YYYY-MM-DD.jsonl[.gz]`) plus a
  `golden_manifest.json` listing every source day with its verify verdict — NOT one physically
  concatenated file. Rationale: `backtest/index.py:group_by_cid_indexed` already accepts a
  directory and merges per-file `.idx` sidecars in ts order (`backtest/index.py:117-124`), and
  issue #281 explicitly leaves this choice to the charter ("either concatenates verified days
  into one canonical file or records the canonical file list in a manifest"). Day-level
  quarantine stays possible and no multi-GB rewrite is ever needed.
- **Quality bar (concrete numbers on existing metrics):**
  - Every golden day: `verify_tick_data` → `status == "PASS"` and
    `capture_state().label == "COMPLETE CAPTURE"`.
  - The golden set as a whole: `readiness.level == "RESEARCH_READY"` **with headroom** —
    all 10 series (`strategy/series.py:SERIES`), `min_market_duration_pairs == 10`,
    `time_blocks >= 5` days (policy floor: 3), `windows_count >= 500` (floor: 100),
    `min_windows_per_market >= 50` (floor: 10), `sampling_gap_rate <= 0.05` (ceiling: 0.10),
    `corrupt_rate == 0`, `collector_error_rate == 0`.
  - Continuity budgets per window: late starts `<= 5s` and early cutoffs `<= 5s`
    (`verify_window_continuity`, `scripts/verify_tick_data.py:336-341`); zero time reversals
    (the 2026-09-15 double-writer incident produced 2,799 — `scripts/collector_watchdog.py:57-61`).

## Deliverable 2 — Collection plan
- Watchdog-guarded capture: `python -m scripts.collector_watchdog` keeps
  `python -m scripts.collect_ticks` alive (restart on crash, kill+restart on wedged manifest).
- Duration: 5 consecutive full UTC days (`--days` rotation via `now_day_key`,
  `scripts/collect_ticks.py:369`). Expected volume ~1.5–1.7GB/day raw (~160k snaps/day).
- **Freeze rule:** no collector-code changes while a golden capture is running (protects
  replay-grade provenance; relevant to the deferred #174).
- Per-day acceptance gate: after each day closes, run
  `python -m scripts.verify_tick_data run/ticks/ticks_<day>.jsonl`; failing days are excluded
  (recorded with reason), never silently mixed in.

## Deliverable 3 — Certification plan
- Command sequence that must pass (see plan TASK-3); promotion/re-certification policy:
  the golden set is re-certified whenever a day is added, replaced, or a verify policy version
  changes (`READINESS_POLICY_VERSION`, `scripts/verify_tick_data.py:32`); the manifest records
  the policy version of the certification run.

## Deliverable 4 — Replay-speed budget
- Budget: full-dataset sweep wall-time ≤ ~1s per window on a warm `.idx` sidecar (measured
  sidecar cost ~50ms per cid jump vs ~1.5s/day full scan, `backtest/index.py:1-9`); a full
  no-index scan of a 467MB file costs ~34s (`docs/measurements/issue-221-gil-contention.json`)
  and is out of budget — the golden certification therefore requires fresh `.idx` sidecars for
  every golden day (`backtest.index.is_fresh`).

## Deliverable 5 — Name registry
- `docs/glossary.md` → new "Data" entry: **the golden dataset** (with code pointer to the
  charter doc).
- `AGENTS.md` → one line in the `docs/` listing pointing at the charter.

## Out of scope (explicit)
- Writing/running the capture, assembling the file set, building sidecars (that is #281).
- Changing verify thresholds, the collector, or any backtest code.
- Deciding #279's UI behavior (that issue stays as scoped; the golden set simply becomes its
  most-qualified candidate once it exists).
