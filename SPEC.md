# SPEC.md — Issue #359: Per-Delta Socket-Book Reconciliation Replay

## 1. Context & Motivation

In Issue #174 Phase 1 and Issue #350, empirical measurement demonstrated a **29.78% divergence rate** between the maintained WebSocket order book (`CLOBMarketWSClient`) and REST ground truth (`full_book()`), with worst-case gaps reaching 32 cents on 1-cent spread markets. This divergence blocks Phase 2 (provenance switch to socket book).

Aggregate statistics alone cannot distinguish between:
1. `price_change` delta application bugs (e.g., missed levels, price grid shifts, or stale entries never removed);
2. Post-reconnect resynchronization gaps (serving uninitialized or partial books between reconnect and the next snapshot);
3. `tick_size_change` grid mutations;
4. Discrepancies between venue in-frame `best_bid`/`best_ask` and reconstructed level maps.

Issue #359 builds an offline per-delta reconciliation replay tool to replay recorded socket events one at a time through `CLOBMarketWSClient`, reconcile against REST ground truth after every event, attribute divergences to specific event types, and isolate the smallest breaking event sequence as a regression fixture.

---

## 2. Requirements & Acceptance Criteria

### In Scope
1. **Diagnostic Raw Capture Tool / Session Runner**:
   - Provide a bounded diagnostic recording tool (`scripts/record_raw_socket_session.py`) that captures raw WebSocket event frames alongside 1s-cadence REST ground truth books for active tokens across market series.
   - Output structured, timestamped JSONL records joinable by token and wall-clock time.
2. **Offline Per-Delta Replay Harness**:
   - Implement `scripts/replay_socket_reconciliation.py` capable of feeding captured event sequences into an isolated `CLOBMarketWSClient` (using pure in-memory message dispatch without live network).
   - Reconcile after each event against REST ground-truth snapshots and in-frame top-of-book quotes (`best_bid`, `best_ask`).
3. **Per-Event Attribution Report**:
   - Produce a quantitative divergence report detailing:
     - Total events processed by type (`price_change`, `book`, `best_bid_ask`, `tick_size_change`, `last_trade_price`).
     - Event counts and divergence counts per event type.
     - Attribution percentage naming the specific event type that breaks the book.
     - First divergence point and distance in events from preceding snapshot.
4. **Minimal Reproducible Fixture**:
   - Extract the smallest reproducible breaking event sequence into `tests/fixtures/socket_divergence_smoking_gun.json`.
5. **Contract Test Anchor**:
   - Add contract tests in `tests/test_clob_ws_collector.py` executing the fixture through `CLOBMarketWSClient`, proving that the failure mode is cleanly isolated and reproducible.
6. **Documentation & Handoff**:
   - Document findings and name the defect mechanism directly for the follow-up fix issue.

### Out of Scope
- Modifying `apply_price_change`, `apply_book_snapshot`, or reconnect logic in production (reserved for the follow-up fix issue).
- Modifying the live collector (`scripts/collect_ticks.py`) default recording format or default behavior.
- The Phase 2 provenance switch itself.

---

## 3. Architecture & Data Flow

```
+-------------------------------------------------------------+
| Real Venue Capture: scripts/record_raw_socket_session.py    |
| - Connects CLOBMarketWSClient, dumps raw events             |
| - Polls REST book snapshots on 1s cadence                   |
| - Writes: run/diag_ws/raw_events_YYYY-MM-DD.jsonl           |
+-------------------------------------------------------------+
                              |
                              v
+-------------------------------------------------------------+
| Replay Engine: scripts/replay_socket_reconciliation.py      |
| - Pure offline replay: loads raw JSONL events               |
| - Feeds each event into fresh CLOBMarketWSClient            |
| - Reconciles book after each event against REST & in-frame  |
+-------------------------------------------------------------+
         |                                           |
         v                                           v
+-----------------------------+     +-------------------------------+
| Attribution Report          |     | Minimal Breaking Fixture      |
| - Event-type breakdown      |     | tests/fixtures/               |
| - Divergence rates per type |     |   socket_divergence_          |
| - Names breaking event type |     |   smoking_gun.json            |
+-----------------------------+     +-------------------------------+
                                                     |
                                                     v
                                    +-------------------------------+
                                    | Contract Regression Test      |
                                    | tests/test_clob_ws_collector  |
                                    +-------------------------------+
```

---

## 4. Verification Gate

- Run targeted tests: `python -m pytest tests/test_clob_ws_collector.py -q`.
- Verify replay execution with zero network dependency.
- Confirm zero changes to production trading or live collector routines.
