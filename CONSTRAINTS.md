# CONSTRAINTS.md — Quality Bar & Boundaries for Issue #438

## 1. Zero Regressions

- `python -m pytest tests/test_replay_socket_reconciliation.py -q` must pass — the 8 existing
  tests pin the reconciliation semantics, including the intentional REST-skip guard
  (`:264`). Adding fields must not change any existing assertion's outcome.
- `python -m pytest tests/test_record_raw_socket_session.py -q` must pass — new file created
  by this issue (the recorder has no test coverage today).
- `python -m pytest tests/test_clob_ws_collector.py -q` must pass — it covers the collector's
  shadow comparison, which this issue reads but must not change.
- Green at the final verification gate is the PR condition; a red test inside a task is the
  intended TDD step.

## 2. Performance

- No production hot-path change. The recorder is an offline diagnostic; the replay is offline.
- The replay must stay single-pass over the session file: per-series and per-bucket
  aggregation accumulate during the walk, never a second read of a 24 MB+ file.
- No new external dependencies.

## 3. Anti-Cheat

- No skipping, `xfail`-ing, or weakening assertions. The existing REST-skip test is **prior
  intended behaviour** and is not to be loosened to make a new bucket easier to populate.
- No change to `BOOK_SHADOW_TOLERANCE` (0.001) or the replay's `TOLERANCE` (0.001) — that is
  the successor re-gate issue's territory.
- No `except Exception` that swallows: the recorder's REST path must become **louder**, never
  quieter. Converting a silent skip into another silent skip fails this issue's purpose.
- Every reported number names its instrument and its population. A rate quoted without saying
  which comparison path produced it is a defect in the deliverable.

## 4. Boundaries — do not touch

- `scripts/collect_ticks.py` — the collector's live shadow telemetry is read, not changed.
- `strategy/streaming.py` — `CLOBMarketWSClient` production behaviour is out of scope
  (that is #440).
- `server/osc_dash.py:4524` — the dashboard's duplicate `BOOK_SHADOW_TOLERANCE` copy is not
  unified here.
- The frozen Phase 1 text and numbers in `docs/issue-174-socket-book-disagreement.md` — the
  verdict is **appended**, never rewritten.
- `run/diag_ws/raw_session_2026-09-30_04-57-51.jsonl` — the historical capture stays
  byte-identical; it is evidence, not an input to be mutated.
- `backtest/engine.py` — `tick_size` is read for the bucket edges, not changed.

## 5. Invariants

- The replay's divergence rule stays **strictly greater than** tolerance. A bucket edge must
  not silently redefine "divergent".
- A session with `rest_snapshot_count == 0` must be **impossible to mistake** for a
  successful capture.
- The replay must remain able to run the existing fixture
  (`tests/fixtures/socket_divergence_smoking_gun.json`) unchanged, so #359's reproduction
  keeps working.
- The bimodal split is reported **per series** — a single global rate is not the deliverable.
