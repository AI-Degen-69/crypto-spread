# tasks/plan.md — Issue #438

Branch: `i438/root-cause-ws-vs-rest-book-divergence` | Issue: #438

**Size:** Standard — 2 scripts + 1 doc + tests/fixtures, one architectural decision (what a
usable capture is allowed to be).
**Task type:** Research (primary) + Debug + Code (instrumentation only).

## CodeRabbit intake note

No plan was received: the issue has **zero comments** (`gh issue view 438 --json comments` →
empty), so no `@coderabbitai plan` prompt was ever posted for it — unlike #467/#468 created by
`create-issue` intake. Nothing was adopted, rejected, or left `[UNVERIFIED]` from a plan
comment. Every seam below was verified directly against the code in this session.

## Open questions — resolved from code (Step 0A)

1. **Is the REST capture path broken?** **No — it has never run.** The only session on disk
   (`run/diag_ws/raw_session_2026-09-30_04-57-51.jsonl`) is 35,333 lines, **all `type: "ws"`, zero
   `type: "rest"`** (measured). The file's mtime is `2026-09-30 07:58:18 +0300`; the recorder's
   REST path landed in `b3a44cf` at `2026-09-30 08:35:10 +0300` — **37 minutes after the capture**.
   So the 0-REST session is a chronology artefact, and "the REST pillar has never been exercised"
   is the accurate statement.
2. **Why was a fully-failed REST side silent?** Two silent branches in
   `scripts/record_raw_socket_session.py`: `if b:` skips a falsy book with **no log**, and the
   `except` swallows to `log.warning`. The run summary does print `rest_snapshot_count`, but
   nothing asserts it is non-zero. Fixed by T1.
3. **Does the replay's REST pillar exist?** Yes, and it is complete but unexercised:
   parse `:162`, store `:170-178`, temporal select `:180-190`, per-type divergence `:295-317`.
   A REST-bearing test already exists (`tests/test_replay_socket_reconciliation.py:264`) using
   synthetic records.
4. **Will the replay reproduce the issue's 16.8%?** **No, and it must not be expected to.**
   The 16.8% comes from the *collector's* live shadow path (`scripts/collect_ticks.py:556`
   `shadow_compare_book`, called at `:948-952`), which compares a fresh REST book against the
   socket's cached book with **no temporal alignment**. The replay matches within ±0.5 s and
   returns `None` — skipping the comparison — when REST disagrees with the in-frame declared
   quote. Two instruments, two populations. The verdict must name which produced every number.
5. **What is one tick?** `TOLERANCE = 0.001` (`replay_socket_reconciliation.py:40`) and
   `tick_size = 0.001` (`backtest/engine.py:283`); divergence requires *strictly greater* than
   tolerance. Consequence: the issue's "`<= 1 tick`" bucket is **empty by construction**. See the
   improvement proposal.
6. **Is the recorder tested?** No — `tests/test_record_raw_socket_session*.py` does not exist.
   T1 creates it.

## Interface contracts (Step 4)

Additive only; every existing field keeps its meaning and the divergence rule stays
*strictly greater than* `TOLERANCE`.

```
DivergenceRecord  += rest_rx: Optional[float]   # REST snapshot rx selected for this comparison
                     ws_rx: float               # the WS record rx that produced ws_bb/ws_ba
                     tick_bucket: str           # one of the magnitude bucket keys below

ReconciliationReport +=
  per_series: Dict[str, {                       # keyed by series slug where knowable
      "comparisons": int, "rest_divergences": int,
      "divergence_rate": float, "max_gap": float }]
  magnitude_buckets: Dict[str, int]             # over ALL comparable pairs, not only divergent
  skew_buckets: Dict[str, int]                  # {"rest_after_ws", "rest_before_ws", "unknown"}
```

**Magnitude bucket edges** (gap = `max_gap` for the comparison), over every pair where both books
carry at least one comparable best quote:

| key | edge | meaning |
|---|---|---|
| `sub_tick` | `0 < gap <= 1t` | invisible to today's `divergent` rule — measured, never counted as divergence |
| `1_3_ticks` | `1t < gap <= 3t` | the bulk population |
| `>3_ticks` | `gap > 3t` | the violent tail (the issue's 8–9¢ cases) |

where `1t = TOLERANCE`. The issue's own "`<= 1 tick`" bucket is preserved in vocabulary as
`sub_tick` but is **populated from sub-tolerance observations**, not from divergent ones — this is
the improvement proposal below, adopted into the plan.

**Recorder contract** (T1): the session summary prints per-token REST and WS sample counts, and a
capture with `rest_snapshot_count == 0` ends with an explicit `FAILURE` line and a non-zero exit
status instead of a neutral summary. An empty/falsy book and a raised exception are logged as
**distinct** causes.

## Dependency graph

```
T1 ──┬──> T2 ──┐
     └──> T3 ──┴──> T4
```

Risk-first: the riskiest fact in this issue is "can a REST-bearing capture exist at all?", and
it is settled by T2 before any analysis code is trusted.

## Tasks

### T1 — [Code/Debug] Make a REST-less capture impossible to mistake
- **Size:** S · **Files:** `scripts/record_raw_socket_session.py`,
  `tests/test_record_raw_socket_session.py` (new)
- **Depends on:** —
- **Build:** split the `if b:` silent skip into an explicit empty-book log; keep the exception
  path but log the exception type distinctly; print per-token REST/WS sample counts in the
  summary; end a zero-REST session with a `FAILURE` line and non-zero exit. Keep the CLI flags
  (`--seconds`, `--series`, `--out-dir`, `--poll-rest-interval`) unchanged.
- **Verify:** `python -m pytest tests/test_record_raw_socket_session.py -q` — a zero-REST run is
  reported loudly and exits non-zero; a REST-bearing run prints per-token counts. Written RED
  first.
- **Domain tag:** `[Debug]` · **Helper skills:** `debugging-and-error-recovery` (classify the two
  silent branches), `test-driven-development` (RED first on the new file)
- **Status:** ✅ done. `capture_verdict` separates three failures (nothing recorded / WS without
  REST / healthy); `poll_rest_once` counts recorded, empty-book and fetch-error **per token** and
  logs the last two distinctly; the summary prints the per-token table; `main` prints `FAILURE`
  and exits non-zero on a ground-truth-less capture. `run_session` returns a `SessionResult`
  (its only caller is `main`). New file `tests/test_record_raw_socket_session.py`: 10 tests,
  written RED first (`ImportError: cannot import name 'SessionResult'`). No new ruff codes
  versus the module's baseline; the new test file is ruff-clean.

### T2 — [Research] Capture a REST-bearing session (operational)
- **Size:** S · **Files:** `run/diag_ws/<new session>.jsonl` (gitignored evidence)
- **Depends on:** T1
- **Build:** run the hardened recorder for long enough to cover at least one active window per
  target series (crypto up/down markets run continuously, so no market-hours gate). Capture the
  exact command and the printed counts in this file.
- **Verify:** the summary reports `rest_snapshot_count > 0` **and** per-token counts; an offline
  count of `type == "rest"` lines matches. A zero-REST result means **stop and fix the capture** —
  do not analyse it.
- **Domain tag:** `[Debug]` · **Helper skills:** `debugging-and-error-recovery`
  (`doubt-driven-development` if the capture succeeds but the counts look wrong)
- **Status:** ✅ done — **CP1 passed.** `run/diag_ws/raw_session_2026-10-07_00-42-10.jsonl`
  (49.5 MB, gitignored): **71,690 WS events + 680 REST snapshots**, exit code 0, first capture
  with ground truth. Mechanical check on the file: 680 `rest` lines = the summary's count exactly;
  all **10 series** represented (34 snapshots per token, 68 per series); 0 empty books, 0 fetch
  errors. REST timestamps span the WS window (679/680 inside it). The recorder's new per-token
  table printed.
- **First signal from the T3 instrument on this session** (read-only; the verdict is T4's job and
  is **not** written yet): 46 divergences across 27,285 REST comparisons (**0.17%**), not 16.8%.
  They live entirely in `book` (31/104 = 29.8%) and `last_trade_price` (15/54 = 27.8%) events —
  while **`price_change`, the event type #359/#362 blamed, has 0 divergences over 27,127
  comparisons**. Magnitude: 46 pairs in `>3_ticks`, 2 in `1_3_ticks`, 0 `sub_tick`, max gap 5¢.
  Per series, rates are 0.0–0.2% (btc-5m 0.2%, eth-5m 0.2%, sol-15m 0.1%, xrp-5m 0.1%) — the
  issue's "5m series at 26–29% vs btc-15m at 5.2%" does not appear.
- ⚠️ **Instrument gap found while reading that signal, before drawing any conclusion.**
  `note_skew` records the *order* (REST read before/after the WS mutation) but not the *age gap*
  between them. On this capture the ordering is one-sided — `rest_after_ws` 0, `rest_before_ws`
  27,285 — so the ordering data rules out one skew form but cannot by itself rule out staleness in
  the other direction, which is the dominant ordering. Deciding hypothesis 4 properly needs the age
  distribution for divergent versus non-divergent pairs. Flagged for T4 rather than silently
  treated as answered.

### T3 — [Code/Research] Extend the replay with per-series, magnitude and skew instrumentation
- **Size:** M · **Files:** `scripts/replay_socket_reconciliation.py`,
  `tests/test_replay_socket_reconciliation.py`
- **Depends on:** T1
- **Build:** implement the interface contracts above; keep the walk single-pass over the session;
  record `rest_rx`/`ws_rx` per divergence so hypothesis 4 is decidable; add the per-series
  breakdown (today only by event type); add `sub_tick` measurement without changing `divergent`.
- **Verify:** `python -m pytest tests/test_replay_socket_reconciliation.py -q` — new tests for
  bucket edges (a gap exactly at 1t is **not** divergent but lands in `sub_tick`; >3t lands in
  `>3_ticks`), per-series attribution, and skew classification from synthetic records. All 8
  existing tests stay green.
- **Domain tag:** `[Debug]` + `[Backend/Logic]` · **Helper skills:**
  `incremental-implementation`, `test-driven-development`, and `doubt-driven-development` for the
  instrument-versus-instrument doubt recorded in SPEC.md §3
- **Status:** ✅ done. `tick_bucket` expresses the edges in venue ticks (one tick = TOLERANCE);
  `note_magnitude` / `note_series` / `note_skew` aggregate **every** comparable pair, not only the
  divergent ones; `DivergenceRecord` carries `ws_rx`, `rest_rx` and `tick_bucket`;
  `_select_rest_book` now returns the snapshot's receive time alongside the book so skew is
  computable; `print_report_table` gains the bucket, timing and per-series sections. 6 new tests,
  RED first (`ImportError: cannot import name 'tick_bucket'`) → 15 pass.
  **Two things the end-to-end run taught us** (see below), both folded back into the code:
  a zero gap is agreement and is reported as `exact`, kept out of the `sub_tick` drift bucket;
  and per-series attribution needs a REST record to name a token's series, so tokens without one
  report under `unknown` rather than being dropped. Measured on the real 35,333-line session
  (4.1 s, single pass): **69,162 comparable in-frame pairs, all `exact`**, with zero REST
  comparisons — the premise of the issue, made visible by the instrument.

### T4 — [Research/Docs] Name the cause, write the verdict, commit the fixture
- **Size:** M · **Files:** `docs/issue-174-socket-book-disagreement.md`,
  `tests/fixtures/<new fixture>.json`, `tests/test_replay_socket_reconciliation.py`
- **Depends on:** T2, T3
- **Build:** replay the T2 session; report rate **and** buckets per series; rule hypothesis 4 in or
  out with the `rest_rx`/`ws_rx` evidence; name the dominant cause cited by file:line or capture
  evidence; append a new section to the verdict doc **without touching the Phase 1 text**; extract
  the minimal reproducing fixture via the existing `--fixture-out` and add a test that asserts it
  through `CLOBMarketWSClient`.
- **Verify:** the new fixture test passes; the appended section states the cause, the per-series
  split, and hypothesis 4's ruling; `python -m pytest tests/test_replay_socket_reconciliation.py -q`
  passes.
- **Domain tag:** `[Research]` + `[Docs]` · **Helper skills:** `idea-refine` (spike → reasoned
  recommendation), `documentation-and-adrs` (the appended verdict section)

## Checkpoints

- **CP1 — after T2.** A REST-bearing capture exists on disk with per-token counts. This is the
  step-by-step halt: if the capture cannot be produced, the issue stops here rather than
  proceeding to analyse nothing.
- **CP2 — after T3.** The extended report runs over the T2 session and prints the per-series
  split; no verdict written yet.

## Improvement proposal (Step 5) — adopted by default

- **Evidence (verbatim, issue #438):** "extend the replay (or add an offline analysis pass over the
  session) to bucket each divergence by magnitude: `<= 1 tick`, `1–3 ticks`, `> 3 ticks`" — and,
  from the code, `TOLERANCE = 0.001` (`scripts/replay_socket_reconciliation.py:40`) against
  `tick_size: float = 0.001` (`backtest/engine.py:283`), with divergence defined as strictly
  greater than tolerance.
- **Proposal (edge-case hardening → folded in as T3):** the issue's own first bucket cannot ever be
  non-empty, because a difference of exactly one tick is not a divergence. Measure the marginal
  distribution over a **lower** reporting edge (`0 < gap <= 1t`) as `sub_tick`, leaving the
  `divergent` rule untouched. Without this, the issue's central claim — "a large population of
  sub-tick drift plus a rare, violent population" — cannot be tested by the instrument the issue
  proposes to build, and the bimodality would stay an assumption dressed as a measurement.
- **Rejected:** nothing. No CodeRabbit plan arrived to reject or adopt.

## Deliberate deviation from Step 6.6 (recorded)

Sub-issues were **not** created for T1–T4. Repo practice for the comparable Standard work (#465)
was to plan in `tasks/plan.md` without tracker sub-issues, and the repo's open-issue list is
deliberately short (8 open). The tracker stays clean; the plan remains the source of truth.
Revisit if a future issue needs parallel workstreams.

## Build notes for Station III

- The recorder change is the only place a **new test file** is created; keep it hermetic — no
  network in tests (`full_book` and the bridge are injectable/mocked, as
  `tests/test_clob_ws_collector.py` already demonstrates).
- Do not let T3's bucket work touch the collector path; the two instruments stay separate.
- **Code exploration was done inline in Station II**, not via a separately deployed
  `code-explorer` persona: the tier is Standard (not Large) and every seam was verified with
  command output recorded in SPEC.md §3. The `code-explorer` persona is available on disk
  (`~/.agents/agents/code-explorer.md`) if T3 finds the replay's control flow needs mapping.
