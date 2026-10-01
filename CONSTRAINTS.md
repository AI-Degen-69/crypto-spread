# CONSTRAINTS — Issue #373

Locked at Station II (2026-01-10). Holds for this issue only; stale once merged
(see `docs/git-workflow.md` §5).

## Problem (verified, not assumed)
`strategy/markets.py` never defined `GAMMA_HOST` / `CLOB_HOST`, but
`scripts/record_raw_socket_session.py:29` does
`from strategy.markets import CLOB_HOST, full_book`.
Evidence: `python -c "from strategy.markets import CLOB_HOST"` → `ImportError`
(exit 1). The recorder has been unstartable since commit `b3a44cf`.

## In scope
1. Define `GAMMA_HOST = "https://gamma-api.polymarket.com"` and
   `CLOB_HOST = "https://clob.polymarket.com"` in `strategy/markets.py`,
   placed next to `MARKET_TIMEOUT` / `EVENTS_TIMEOUT` (`markets.py:22-23`).
2. Values byte-identical to `scripts/collect_ticks.py:89-90` (the canonical pair).
3. A smoke test that fails on a missing venue constant.

## Out of scope (explicitly NOT here)
- Rewiring `scripts/collect_ticks.py` or `strategy/live_trader.py` to import the
  new constants → that is Issue #374.
- Discovery-logic changes → that is Issue #375.
- The `time.time()` vs `get_real_utc_time()` divergence at
  `scripts/collect_ticks.py:280` → needs an owner decision, untouched here.
- Deleting the legacy copy in `scripts/measure_5m_oscillation.py:37-38`.

## Quality gates
- `python -c "from strategy.markets import CLOB_HOST, full_book"` exits 0.
- `python -c "import scripts.record_raw_socket_session"` exits 0.
- `python -m pytest tests/test_collect_ticks_smoke.py -q` → 39 passed (baseline,
  measured pre-change).
- New smoke test file passes.
- Full suite: **not** run locally — CI is the merge gate (`AGENTS.md` policy).