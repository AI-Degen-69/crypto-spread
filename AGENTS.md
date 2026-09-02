# AGENTS.md — crypto-spread

Independent lab for 5m/15m SPREAD-2 capture on BTC/ETH/BNB/SOL/XRP.

## Stack
- Python, `fastapi` + `uvicorn` + `requests` only (`requirements.txt:3`)
- Data sources: `https://gamma-api.polymarket.com/events?series_slug` and `https://clob.polymarket.com/book`
- PowerShell on Windows — join commands with `;` not `&&`

## Commands
```powershell
pip install -r requirements.txt
pip install pytest                          # dev: 116 tests
python -m pytest -q                         # all tests
python -m scripts.collect_ticks             # capture: full-depth + tape to run/ticks/ticks_YYYY-MM-DD.jsonl (1s poll, 10 series)
python -m scripts.collect_ticks --once      # single poll smoke test
python -m scripts.verify_tick_data run/ticks # verify tick integrity & data quality
python -m scripts.rebuild_windows           # rebuild oscillation_windows.jsonl + summary from real run/ticks
python -m scripts.backtest run/ticks --offset 0.02 --queue 50  # replay
python -m scripts.sweep_backtest run/ticks --mode grid          # quant parameter sweep
python -m uvicorn server.osc_dash:app --host 127.0.0.1 --port 8802  # dashboard
```

## Structure
- `scripts/collect_ticks.py` — primary collector. Polls 10 series (`strategy/series.py:SERIES`), fetches both books + tape via CLOB/data-api, writes replay-grade `run/ticks/ticks_YYYY-MM-DD.jsonl` (full bids/asks + tape_delta per second) + `manifest.json`. Use this for backtests.
- `scripts/verify_tick_data.py` — dataset integrity validator. Verifies JSON syntax, crossed books, timestamp gaps, late starts, mid bounds, and error rates.
- `scripts/sweep_backtest.py` — parameter sweep engine. Runs 1D, 2D, grid, and random quant parameter sweeps across datasets.
- `scripts/rebuild_windows.py` — reconstructs `run/oscillation_windows.jsonl` and `run/oscillation_summary.json` from `run/ticks/*.jsonl` full-depth data.
- `scripts/measure_5m_oscillation.py` — legacy top-of-book collector (best_bid/ask/mid only). Kept for reference; `collect_ticks` is the source of truth for replay.
- `backtest/` — offline replay engine. `engine.py:replay()` is pure (no venue calls), `index.py` builds per-file cid sidecars for slider-speed sweeps.
- `server/osc_dash.py` — FastAPI dashboard on `:8802`. Routes: `/` + `/oscillation`, `/summary` + `/analysis`, `/api/oscillation`, `/api/goals`, `/api/analysis`, `/api/ticks/manifest`, `/api/ticks/verify`, `/api/backtest` (query: offset/queue/pair_cost/exit_* /fill_model), plus live execution & cockpit routes (`/api/live/account`, `/api/live/state`, `/api/live/control`, `/api/live/orders`, `/api/live/cancel_all`, `/api/live/cancel_order`, `/api/live/test_order`).
- `strategy/` — `series.py` (10-series universe, single source), `markets.py` (book/tape fetchers, `LiveMarket`), `live_trader.py` (order flow & execution engine), `config.py:17` (`MakerConfig`) — heavily commented with hunter-fleet values; most fields are legacy, verify against `README.md:22` before reusing.
- `run/` — gitignored (`.gitignore:6`). Contains `ticks/` (replay-grade) and legacy `oscillation_*.jsonl`. Regenerated; do not commit.
- `docs/` — `operations.md` (runbook for capture + replay), `live-dashboard-streaming-spec.md` (RTDS & WebSocket live dashboard blueprint), `research-spread-bot-conclusions.md` (findings), `backtest-optimization-results.md` (sweep report).
- `tests/` — 116 tests: `test_backtest_engine.py` (48), `test_backtest_index.py` (5), `test_collect_ticks_smoke.py` (8), `test_rebuild_windows.py` (4), `test_verify_tick_data.py` (14), `test_osc_dash_integration.py` (14), `test_sweep_backtest.py` (8), `test_docstrings.py` (1), `test_live_trader.py` (14).

## Data Model / Classification
- Window classification in `scripts/measure_5m_oscillation.py:125` (`classify_window`): vs base 0.50, `max_up=max(mids)-0.50`, `max_down=0.50-min(mids)`. `oscillating` = both ≥0.02, `monotonic` = one ≥0.02, `flat` = neither. Thresholds at `OSC_THRESH_CENTS=[2.0,3.0]`.
- Dashboard finding (635 windows): median range 49.5¢, 73% oscillating on 5m, `touch_pair` median ~1.01. Exit thresholds unified to 5¢ (0.05) across all series and window durations.

## Gotchas
- **Polymarket Gasless Operations**: Trading (CLOB orders), CTF pair merges (`mergePositions`), and trading approvals (`setupTradingApprovals`, `approveErc20`, `approveErc1155ForAll`) are sponsored (gasless) when routed via the Polymarket Relayer and smart wallet flow per official docs. Direct on-chain EOA transactions incur native gas. External bridging and wallet funding also incur gas.
- **RTDS Crypto Feed & WebSockets**: `prices.crypto.binance` supports `btcusdt`, `ethusdt`, `solusdt`, `xrpusdt` only (no BNB on Binance or Chainlink RTDS feeds; BNB uses REST fallback). CLOB Market WebSocket (`wss://ws-subscriptions-clob.polymarket.com/ws/market`) supports multi-token subscription on a single socket with 10s PING heartbeat and `custom_feature_enabled` for `best_bid_ask`. Use 1s RTDS ticks as leading price signals for faster stop-loss execution.
- `run/` is in `.gitignore`; missing `run/*.jsonl` means collector hasn't run — dashboard shows empty state, not an error.
- Collector uses a pooled `requests.Session` with `(3.05, 5.0)` timeouts (connect, read) and `max_retries=0` — failed markets are skipped for that poll, not retried.
- `strategy/markets.py` sanitizes slugs via `_SAFE_SLUG_RE` before embedding in HTML/DB; `full_book`/`parse_book` tolerates malformed price rows (counted in `malformed`) but raises `ValueError` on structural payload mismatch.
- No `opencode.json` or `CLAUDE.md` exists — no hidden verification steps to run.
- Dashboard: `server/osc_dash.py` (FastAPI on :8802) is the sole canonical dashboard.
