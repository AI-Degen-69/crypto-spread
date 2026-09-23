# tasks/plan.md — Issue #312: golden dataset research cut

Branch: `i312/golden-dataset-research-cut` | Issue: #312 | Size: Standard | Type: Code + Docs

## Why

The certified golden dataset (6 days, 4,910 windows, 1.43M ticks) is 8–20× above the
certification floors. An OFAT sweep loop (#311) multiplies the ~100-minute full-set sweep
cost dozens of times (a full-set sweep runs ~100 minutes: ~716s load + ~5,260s
simulation over 1.43M ticks). This issue builds a **research cut** — a derived, deterministic,
floor-respecting subset — so sweeps run on minutes of data while the golden set stays
intact as the single canonical dataset.

## Open questions resolved (from code, Station II Step 0A)

- **Sampling unit (issue's open question):** ADOPTED option (b) from the issue — stratify
  by market-duration pair × UTC day × window class (`oscillating`/`monotonic`/`flat`,
  `backtest/engine.py:650`), uniform within the finest cell, fixed seed. Evidence: the
  sweep results in `docs/backtest-optimization-results.md` §0.2 rank configs by PnL where
  pair_rate is the regime variable — the class mix is what drives it, so preserving it
  per stratum keeps the cut's aggregate behavior representative. The guardrail gate
  (±10% tolerance) verifies this empirically.
- **Window identity:** a window is its `cid`; snaps grouped by `group_by_cid`
  (`backtest/engine.py:93`). Selection = pick cids, then emit all snaps of each picked cid.
- **Day attribution:** a cid's snaps may straddle a day boundary (`group_by_cid` docstring,
  `backtest/engine.py:96-97`); a window is attributed to the day of its first snap, and
  its snaps are written whole to that day's cut file (straddling windows live entirely in
  one cut file — no window is split, matching the engine's expectation).
- **Sampled set remains RESEARCH_READY:** floors (`scripts/verify_tick_data.py:34-59`,
  RESEARCH_READY: ≥3 time blocks, ≥100 windows, ≥10/market) are multiplied by the
  multiplier `M` (default 3): ≥1,500 windows, ≥150 per market-duration pair, ≥4 days.
  M=3, not 4: the scarcest golden pair (bnb 15m) holds 159 windows, so M=4's
  200-per-pair floor is infeasible on the certified set (verified 2026-09-23 from
  `golden_manifest.json` market breakdowns). The allocator's market unit is the
  (series, duration) pair — the same unit the verify policy counts.
  The cut has 5 of 6 days by construction + the guardrail below re-verifies.
- **Regression sample guardrail:** replay a fixed seed-chosen sample of windows through
  `replay()` twice (full-set window group vs cut window group) with identical
  `BacktestParams` and assert identical `WindowResult` per-window fields — the cut is a
  byte-preserving window subset, so per-window results must be *identical*, not
  approximate; aggregate ±10% tolerance from the issue applies to the cut-vs-full
  aggregate comparison on the sampled window set (both sides replayed on the same
  windows), which reduces to identity plus aggregate bookkeeping equality.

## Tasks (risk-first)

- [x] **T1 — `scripts/build_research_cut.py` core: selection (M).** Read the golden set
  (read-only) via `group_by_cid_indexed`-equivalent grouping of `iter_ticks` output;
  compute per-cid (day-of-first-snap, series, duration, class label via `_classify` on
  window mids, n_snaps, first_ts); stratified uniform selection with
  `random.Random(seed=0)` on cells = (series, duration, day, class); targets per cell:
  `max(1, round(M × 50 windows_per_market_floor / n_cells_of_that_market))` proportional
  allocation, then a set-level cap at `M × 500` total windows floor (allocator: scale down
  proportionally, never below 1 per non-empty cell). Deterministic: same golden dir +
  same M + same seed ⇒ byte-identical output. · Validate: new
  `tests/test_build_research_cut.py` (determinism, floors at M×, all 10 series present,
  all golden days represented, no golden-file mutation). Size: L. [Backend/Logic]
  Depends on: —
- [x] **T2 — Emission + manifest.** Write cut snaps to `run/ticks/research/` as day files
  named `ticks_<first-snap-day>.jsonl` (append cids sorted by first_ts; json format
  byte-identical to source lines: `json.dumps(line)` of the parsed dict is NOT
  byte-stable — instead copy the RAW source line text for each selected snap, so cut
  lines are byte-identical to golden lines); build fresh `.idx` sidecars
  (`backtest.index.build_index`); write `run/ticks/research/research_manifest.json`:
  source=golden, per-day source file + source_sha256 (from golden manifest), policy
  (stratification cell definition), seed, M, selected window count per cell, totals
  (windows, windows-per-market-min, ticks), and the guardrail results. · Validate:
  manifest schema test + `verify_tick_data run/ticks/research` reports RESEARCH_READY at
  the multiplier. Size: M. [Backend/Logic] Depends on: T1
- [x] **T3 — Guardrail gate.** In-script, post-emission: (a) golden-dir integrity — every
  golden day file's sha256 matches the golden manifest's recorded hash (or is absent from
  the manifest record → recompute both and require cut-build did not touch them via
  mtime/size snapshot); (b) replay parity — group the cut's snaps with the same
  `group_by_cid` and replay the sampled windows with baseline `BacktestParams`; compare
  against a replay of the same windows loaded from the golden set: per-window
  `WindowResult` fields must be identical (same bytes ⇒ same result); record both sides'
  aggregates (pair_rate, exit_rate, pnl_cents) in the manifest. Any mismatch → hard fail
  with a loud message and no partial manifest claim. · Validate:
  `test_build_research_cut.py::test_guardrail_parity` + integrity test. Size: M.
  [Backend/Logic] Depends on: T2
- [x] **T4 — CLI + docs.** `python -m scripts.build_research_cut --multiplier 3 --seed 0
  --out run/ticks/research` (flags: `--multiplier`, `--seed`, `--out`, `--verify-only`);
  docs: name the cut as derived + non-canonical in `docs/golden-tick-dataset.md` (short
  §: where it lives, build command, "final claims re-run on the full golden set" rule),
  cross-reference from `docs/glossary.md` Data table (new entity name: **the research
  cut**). · Validate: CLI help smoke test + docs consistency read. Size: S. [Docs]
  Depends on: T3

## How we verify

- Targeted suites only: `python -m pytest tests/test_build_research_cut.py
  tests/test_verify_tick_data.py tests/test_build_golden_dataset.py -q`
- The issue's acceptance check: `python -m scripts.verify_tick_data run/ticks/research`
  → RESEARCH_READY; windows ≥ 1,500; windows-per-market ≥ 150; all 10 series; all
  source days represented.
- Golden set untouched: sha256 of every golden day file identical before/after build.

## Out of scope

- Running any sweep; modifying golden charter gates or certification sequence; a
  configurable subsetting DSL; `.gz` output; index format changes.

## Improvement proposals

- ADOPTED (simplification/edge-case, evidence: issue body "One **guardrail gate** at the
  end of the script (cheap, must-pass): replay ... compare aggregate metrics — the cut
  must reproduce the full set's behavior within a stated tolerance band"): tightened the
  guardrail from a ±10% aggregate band to per-window identity (byte-preserving subset ⇒
  replay results must be identical) + aggregate bookkeeping equality. Stronger check,
  same cost.
- REJECTED (none): no further proposals — no additional evidence found in issue or code.
