# Todo Checklist — Issue #438

- [x] T1 [Debug] Make a REST-less capture impossible to mistake — done: `capture_verdict` + per-token counts + non-zero exit; new `tests/test_record_raw_socket_session.py` (10 tests, RED first)
- [x] T2 [Debug] Capture a REST-bearing session — done: `run/diag_ws/raw_session_2026-10-07_00-42-10.jsonl`, 71,690 WS + **680 REST** snapshots, exit 0, all 10 series, 0 empty / 0 errors
- [x] **CP1** — PASSED: 680 REST snapshots (34/token, all 10 series), matching the file's own `rest` line count exactly
- [x] T3 [Debug/Backend] Extend the replay: per-series + magnitude buckets + `rest_rx`/`ws_rx` skew delta — done (6 new tests, RED first; 15 pass). Real-session measurement: 69,162 in-frame pairs, all `exact`, 0 REST comparisons
- [x] T3b [Debug/Backend] Age-gap instrumentation (discovered at CP1, approved) — done: 4 new tests; **46/46 divergences in the two oldest reference-age buckets, 0 below 250ms** across 6,745 comparisons
- [x] **CP2** — the extended report prints the per-series split over the T2 session
- [x] **CP3** — PASSED: the age effect is not a proxy for the frame type (the gradient is present as guard rejection on the gated type, 10.4%→35.0%), and the guard is active on 38,005/38,005 in-window `price_change` pairs, so that row's 0.0% is a consequence, not a coincidence
- [x] T4 [Research/Docs] Verdict appended as §7 of `docs/issue-174-socket-book-disagreement.md` (Phase 1 untouched) + fixture `tests/fixtures/socket_rest_reference_staleness.json` + 4 tests (3 RED first)
- [ ] Final gate: `python -m pytest tests/test_replay_socket_reconciliation.py tests/test_record_raw_socket_session.py tests/test_clob_ws_collector.py -q`

## The named cause (T4)

- **Cause:** the residual is an artefact of the *comparison instrument*, not a defect in the socket book. The maintained book matched the venue's own declared quote on every frame carrying one (0 in-frame divergences / 70,869 events). All 46 counted divergences sit in the 158 comparisons where the concordance guard (`replay_socket_reconciliation.py:365`) is blind because the frame declares no quote: `book` 31/104, `last_trade_price` 15/54 — versus **0/27,127** on the fully-declared frame type.
- **Hypothesis 4: RULED IN**, with a gradient — disagreement is monotone in the REST reference's age (guard rejection 10.4%→21.7%→27.2%→35.0%; counted divergence on `book` 0%→0%→20.7%→41.7%), and zero below ~100 ms.
- **Three planning assumptions fell to the instrument:** the assumed sub-tick drift population is **empty** (0 pairs in `(0,1 tick]` of 169,023 — it is *exact agreement or a whole-cent jump*); the per-series spread does not reproduce (0.0–0.2%, max $0.05 vs the issue's 26–29% / 5.2%); the ±0.5 s window rejects 103,094 candidates (72%).
- **New ledger rows:** N2 (buckets in `TOLERANCE` units make `1_3_ticks` unreachable: 2/169,023), N3 (the metric cannot say whether a pair was checkable — #440's re-scoping target).
- **Fixture-schema gap:** an extracted REST divergence could not reproduce itself (no reference in the #359 shape); `rest_reference`/`ws_rx`/`age_s` added, REST-sourced only.

## Verified at planning (do not re-derive)

- Only session on disk: 35,333 lines, **all `ws`, 0 `rest`**; written 07:58, REST path committed 08:35 — 37 min later.
- Recorder silent branches: `if b:` (no log) and `except → log.warning`.
- Replay REST pillar (line numbers as of base `02356fd`, before this branch moved them): parse `:162`, store `:170-178`, temporal select `:180-190`, divergence `:295-317`; synthetic REST test at `tests/test_replay_socket_reconciliation.py:264`.
- The 16.8% comes from the collector's unaligned `shadow_compare_book` (`collect_ticks.py:556`, called `:948-952`) — **not** reproducible by the replay by design.
- `TOLERANCE == tick_size == 0.001` ⇒ the issue's "`<= 1 tick`" bucket is empty by construction.
