# TODO — Issue #264: Backtest Sweep Visual

- [x] TASK-1 [Backend/Logic]: Lock endpoint response, validation, busy-guard, and release contracts with focused tests.
- [x] TASK-2 [Backend/Logic]: Add Sweep Visual series label metadata and display titles as aligned `05m BTC` / `15m BTC`, while preserving the sweep response and replay behavior.
- [x] TASK-3 [Design/UI]: Render numeric X-axis aggregate/per-market Chart.js views with friendly titles and best-point metadata.
- [x] TASK-4 [QA/Regression]: Browser-verify the 2026-09-18 dataset and run the targeted pytest gate; record caveats.

## Issue #264 verification gate
- [x] `python -m pytest tests/test_osc_dash_integration.py tests/test_theme_tokens.py -q`
- [x] Browser/UI run against `run/ticks/ticks_2026-09-18.jsonl`; confirmed 1 aggregate + 10 market charts and aligned labels.
- [x] Confirm Sweep Visual titles use `05m BTC` / `15m BTC`, and unrelated Issue #174 and presentation/artifact changes are absent from the implementation diff.

### Browser caveat
The 2026-09-18 file contains approximately 24,091 snapshots and the browser run grouped 30 windows. The visual completed with zero-filled cards for markets absent from the selected sample where applicable.

---


## Phase 1 — shadow comparison, zero behaviour change
- [ ] TASK-1 [Backend/Logic]: Shadow WS book capture in the collector.
- [ ] TASK-2 [Backend/Logic]: Disagreement metrics + tick-file fields.
- [ ] TASK-3 [Backend/Logic]: Operator-visible disagreement surface.
- [ ] Checkpoint: measurement live — `slow_tick` unchanged, REST still source, rate readable.

## Phase 2 — switch, gated on Phase 1 evidence
- [ ] TASK-4 [Backend/Logic]: REST reconciliation + WS quoting source (blocked on recorded measurement run).
- [ ] TASK-5 [Debug/Resilience]: Reconnect-stale + crossed-book safety, proved by test.
- [ ] TASK-6 [Backend/Logic]: Live/paper quoting through the shared `book_math` interface.
- [ ] Checkpoint: switch safe — resync proved, fallback proved, freshness bounded by venue.

## Phase 3 — collector switch (perf)
- [ ] TASK-7 [Performance]: Collector skips REST books when socket authoritative; round-time drop measured.
- [ ] TASK-8 [QA/Regression]: Targeted regression gate incl. tick-file backward compat.
