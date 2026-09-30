# Plan: Issue #359 — diag(market-data): per-delta socket-book reconciliation replay — name the event type that breaks the #174 book

Branch: `i359/diag-market-data-per-delta-socket-book-reconcil` | Issue: #359

## Overview & Goal
Build an offline per-delta reconciliation replay harness to isolate why the WebSocket order book maintained by `CLOBMarketWSClient` diverges from REST ground truth by ~30% (Issue #174 Phase 1, Issue #350). The replay harness feeds recorded events one at a time to an offline `CLOBMarketWSClient`, reconciles after each event against concurrent REST book snapshots, attributes divergence to specific event types (`price_change`, `book`, `tick_size_change`, `best_bid_ask`), produces a quantitative attribution report, and captures the smallest breaking event sequence as a fixture with a contract regression test.

## Classification & Context
- **Size Tier**: Standard (2–4 files: diagnostic capture script, replay engine, fixture, test anchor).
- **Task Type**: Debug / Code.
- **Stack**: Python 3.11+ / PowerShell on Windows / `pytest`.
- **Targeted Test Gate**: `python -m pytest tests/test_clob_ws_collector.py -q`.

## CodeRabbit & Open Questions Intake
- CodeRabbit plan comment: None present on Issue #359.
- Open questions resolved:
  - *Are raw socket frames currently recorded in `run/ticks/`?* No. `scripts/collect_ticks.py` only persists REST books and tape prints; `scripts/record_ws_deltas.py` only persists filtered top-of-book quotes. A dedicated diagnostic capture runner is required to gather the raw socket event stream alongside REST snapshots.
  - *Does this issue modify live collector or trading engine behavior?* No. Strictly out of scope. This issue delivers diagnosis, attribution, a smoking-gun fixture, and a regression test contract.

## Improvement Proposal (Adopted by default)
- **Evidence**: In `strategy/streaming.py:534-563`, each `price_change` frame carries the exchange's in-frame `best_bid` and `best_ask` alongside per-level deltas, while `apply_price_change` mutates local dicts and recomputes `max(bids.keys())` without clearing out-of-range levels.
- **Proposal**: Have the replay reconciliation harness compare local reconstructed book state against BOTH concurrent REST snapshots AND the venue's immediate in-frame `best_bid`/`best_ask`. This distinguishes intra-second reducer corruption from exchange-side REST-vs-WS latency instantly.

## Dependency Graph
```
Task 1 (Diagnostic Capture) --> Task 2 (Replay Engine & Reconciler) --> Task 3 (Diagnosis & Fixture Extraction) --> Task 4 (Contract Test)
```

## Task Breakdown

### Task 1: Bounded Raw WebSocket & REST Ground Truth Diagnostic Capture Tool [Backend/Logic] [x]
- **Size**: S
- **Target Files**: `scripts/record_raw_socket_session.py`
- **Helper Skill**: `api-and-interface-design`, `incremental-implementation`
- **Depends on**: None
- **Description**:
  Create `scripts/record_raw_socket_session.py` to record complete raw JSON WebSocket messages from Polymarket CLOB Market WS alongside 1-second REST book snapshots (`full_book()`) for active tokens. Supports `--seconds <N>` (e.g. 60s) or `--events <N>` bounds. Writes to `run/diag_ws/raw_events_YYYY-MM-DD.jsonl`.
- **Verification**: Run `python -m scripts.record_raw_socket_session --seconds 5` smoke test; verify written JSONL rows carry both raw WS frames and REST snapshots.

### Task 2: Offline Per-Delta Reconciliation Replay Engine [Backend/Logic] [x]
- **Size**: M
- **Target Files**: `scripts/replay_socket_reconciliation.py`
- **Helper Skill**: `test-driven-development`, `api-and-interface-design`
- **Depends on**: Task 1
- **Description**:
  Implement `scripts/replay_socket_reconciliation.py`. Reads raw event streams, drives an offline `CLOBMarketWSClient` instance with each event sequentially without network, and performs reconciliation:
  1. Reconciles after every event against current REST snapshot (if matching timestamp/token exists) and against the event's in-frame `best_bid`/`best_ask`.
  2. Tracks divergence counts and divergence rates partitioned by event type (`price_change`, `book`, `best_bid_ask`, `tick_size_change`).
  3. Detects the exact transition where divergence occurs (distance from preceding snapshot, event sequence leading to divergence).
  4. Prints a summary table and JSON attribution report.
- **Verification**: Run `python -m scripts.replay_socket_reconciliation --help` and execute replay on captured event samples.

### Task 3: Session Diagnosis, Event Attribution & Fixture Extraction [Debug] [x]
- **Size**: S
- **Target Files**: `tests/fixtures/socket_divergence_smoking_gun.json`, `docs/issue-359-socket-divergence-attribution.md`
- **Helper Skill**: `debugging-and-error-recovery`, `doubt-driven-development`
- **Depends on**: Task 2
- **Description**:
  Capture or process a real market session with divergence, execute the reconciliation replay, produce the quantitative attribution report naming the offending event type, and extract the minimal breaking sequence into `tests/fixtures/socket_divergence_smoking_gun.json`.
- **Verification**: Verify that the fixture reproduces the book divergence deterministically in an isolated Python session.

### Task 4: Regression Contract Test Anchor [Core/Testing] [x]
- **Size**: S
- **Target Files**: `tests/test_clob_ws_collector.py`
- **Helper Skill**: `test-driven-development`
- **Depends on**: Task 3
- **Description**:
  Add contract test `test_socket_divergence_smoking_gun_reconciliation()` in `tests/test_clob_ws_collector.py`. Loads `tests/fixtures/socket_divergence_smoking_gun.json`, replays through `CLOBMarketWSClient`, and asserts that the failure mode is cleanly anchored and diagnosed.
- **Verification**: `python -m pytest tests/test_clob_ws_collector.py -k test_socket_divergence_smoking_gun_reconciliation -q` passes.

## Checkpoints
- **Checkpoint 1 (after Task 2)**: [x] Offline replay engine functional on test vectors and mock events.
- **Checkpoint 2 (after Task 4)**: [x] Smoking-gun fixture captured, test passing, report generated, ready for review.
