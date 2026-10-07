# Todo Checklist — Issue #438

- [x] T1 [Debug] Make a REST-less capture impossible to mistake — done: `capture_verdict` + per-token counts + non-zero exit; new `tests/test_record_raw_socket_session.py` (10 tests, RED first)
- [x] T2 [Debug] Capture a REST-bearing session — done: `run/diag_ws/raw_session_2026-10-07_00-42-10.jsonl`, 71,690 WS + **680 REST** snapshots, exit 0, all 10 series, 0 empty / 0 errors
- [x] **CP1** — PASSED: 680 REST snapshots (34/token, all 10 series), matching the file's own `rest` line count exactly
- [x] T3 [Debug/Backend] Extend the replay: per-series + magnitude buckets + `rest_rx`/`ws_rx` skew delta — done (6 new tests, RED first; 15 pass). Real-session measurement: 69,162 in-frame pairs, all `exact`, 0 REST comparisons
- [x] T3b [Debug/Backend] Age-gap instrumentation (discovered at CP1, approved) — done: 4 new tests; **46/46 divergences in the two oldest reference-age buckets, 0 below 250ms** across 6,745 comparisons
- [x] **CP2** — the extended report prints the per-series split over the T2 session
- [ ] T4 [Research/Docs] Verdict appended to `docs/issue-174-socket-book-disagreement.md` + fixture + test
- [ ] Final gate: `python -m pytest tests/test_replay_socket_reconciliation.py tests/test_record_raw_socket_session.py tests/test_clob_ws_collector.py -q`

## Verified at planning (do not re-derive)

- Only session on disk: 35,333 lines, **all `ws`, 0 `rest`**; written 07:58, REST path committed 08:35 — 37 min later.
- Recorder silent branches: `if b:` (no log) and `except → log.warning`.
- Replay REST pillar: parse `:162`, store `:170-178`, temporal select `:180-190`, divergence `:295-317`; synthetic REST test at `tests/test_replay_socket_reconciliation.py:264`.
- The 16.8% comes from the collector's unaligned `shadow_compare_book` (`collect_ticks.py:556`, called `:948-952`) — **not** reproducible by the replay by design.
- `TOLERANCE == tick_size == 0.001` ⇒ the issue's "`<= 1 tick`" bucket is empty by construction.
