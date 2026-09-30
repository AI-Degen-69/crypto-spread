# Plan — Issue #349: feat(dash): surface the #174 book_shadow disagreement rate in the collector status card

Branch: i349/collector-status-book-shadow | Issue: #349

## Classification
- Size tier: **Small** (one file, `server/osc_dash.py`; read path over data that already
  exists; no behavior change in the collector).
- Task type: **Code** (API + UI presentation). No Debug component — the metric is
  already computed and persisted by #174 Phase 1 (merged, PR #347).
- Stack: Python / FastAPI + inline HTML/JS SPA; pytest targeted suites.

## CodeRabbit plan intake
- No `coderabbitai` plan comment on the issue. (Verified — issue body only.)

## Open questions resolved from code
- The `book_shadow` block in `manifest.json` carries exactly what the issue asks to
  surface, plus derived ready-to-read fields recomputed each round
  (`scripts/collect_ticks.py:636-644`): `comparisons`, `divergent`, `divergence_rate`,
  `tolerance`-equivalent (`BOOK_SHADOW_TOLERANCE = 0.001`, collect_ticks.py:568),
  `mean_abs_bb_delta`, `mean_abs_ba_delta`, `abs_*_sum`, `max_bb`, `max_ba`,
  `per_series` (comparisons/divergent per series slug). No `tolerance` key is written —
  the API should carry the constant name/value explicitly so the UI can say
  "diverged if delta > 0.001".
- `/api/collector/status` (`server/osc_dash.py:3339-3393`) already parses `manifest.json`
  inside a try/except that swallows malformed JSON — the new field follows the same
  tolerance (`pass` → field stays `null`).
- The collector status card lives in the header (`server/osc_dash.py:4723-4731`):
  `collectorBadge`, `tapeBadge`, `globalStreamPill` share one row and are refreshed by
  `refreshCollectorStatus()` (`osc_dash.py:6558-6638`) on the same poll — a third badge
  slot (`shadowBadge`) fits the existing pattern exactly.
- Empty state: `book_shadow` is absent from the manifest until the first comparable
  tick (both books priced on both sides). A missing block must render "not enough data
  yet", never `0%`.
- Live manifest on disk confirms the real shape: 39,414 comparisons, 11,737 divergent,
  `divergence_rate` ≈ 0.2977, per-series map present.

## Interface contract (locked before build)
- `/api/collector/status` response gains one key: `book_shadow` —
  `null` when the manifest lacks the block or is malformed;
  otherwise `{comparisons, divergent, divergence_rate, tolerance, mean_abs_bb_delta,
  mean_abs_ba_delta, max_bb, max_ba}` (flat copy of the manifest's ready-to-read
  fields; `tolerance` supplied by the server as 0.001). The response change is
  additive — existing keys untouched, existing tests keep passing.
- UI: third badge `shadowBadge` next to `tapeBadge`, refreshed by the existing
  `refreshCollectorStatus()` poll. Renders:
  - rate + raw comparison count together (never a rate without its sample size);
  - "not enough data yet" when `book_shadow` is null or `comparisons == 0`;
  - amber styling when divergent, same palette as the tape alert badge.

## CONSTRAINTS.md (quality guardrails)
- Zero regressions: `tests/test_osc_dash_integration.py` (collector status subset) and
  `tests/test_clob_ws_collector.py` must pass.
- Additive API change only; corrupt manifest must not take down the endpoint.
- No new external dependencies; anti-cheat rules as usual.
- Never render a rate without its denominator (issue acceptance criterion).

## Improvement proposal (Step 5, evidence-based)
- Evidence (verbatim, issue body): "Show the rate prominently and the raw comparison
  count beside it, so a low rate over 20 samples cannot be misread as a low rate over
  200,000." And the live manifest carries `per_series` counts.
- Proposal (hardening, adopt-by-default): the badge tooltip lists the per-series
  divergence breakdown, so an operator hovering the badge can see *which* series
  diverge (e.g. sol-5m 41% vs btc-15m 15%) — no UI real estate cost, directly serves
  the issue's goal of reading the number where the operator is. Rejected for now:
  rendering per-series rows in the card itself (scope creep — the issue asks for a
  rate + count).

## Tasks (dependency graph, atomic)

### T1 [x] [Backend/Logic] API: expose book_shadow summary in /api/collector/status — S
- Files: `server/osc_dash.py` (`api_collector_status`).
- Read `book_shadow` from the already-loaded manifest dict; build the flat summary
  with server-supplied `tolerance`; `null` when absent/malformed.
- Verification: targeted test — status returns the block when the manifest carries it
  and degrades to null when absent; response stays additive.
- Depends on: —

### T2 [x] [Design/UI] UI: shadowBadge next to tapeBadge — S
- Files: `server/osc_dash.py` (header badge HTML + `refreshCollectorStatus`).
- Badge shows `Book Δ: <rate> (N)` with amber color when divergent; honest
  "not enough data yet" state; per-series breakdown in the tooltip.
- Verification: API-driven DOM check (badge text/format from the endpoint payload).
- Depends on: T1.

### T3 [x] [Test] Tests: present/absent/malformed + UI format — M
- Files: `tests/test_osc_dash_integration.py` (collector status subset).
- Present block → summary with rate+count; absent block → `book_shadow: null`;
  malformed manifest → endpoint still answers; zero comparisons → UI format shows
  "not enough data".
- Depends on: T1.

## Checkpoints
- After T2: API + badge work end-to-end against a real manifest shape.

## Out of scope
- Any change to the comparison itself (tolerance, call sites) — #174 Phase 1.
- Collector behavior — #351 (merged) already owns the collection path.
