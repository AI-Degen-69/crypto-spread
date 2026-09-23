# AGENTS.md — crypto-spread

Independent lab for 5m/15m SPREAD-2 capture on BTC/ETH/BNB/SOL/XRP.

## Naming

`docs/glossary.md` is the agreed name for every entity here — the two engines, the two execution
modes, the five dashboard tabs, the data artifacts, and the terms that have already caused bugs
(`mid` vs the recorded one-sided `"mid"` field, tuning knob vs structural limit). Read it before
naming anything in a comment, a commit message or a chat reply. Where it disagrees with an older
code comment, the glossary wins.

Note in particular: **"live" is not a name for the trading engine.** The engine runs in both
execution modes; `mode="live"` means real money.

## Stack
- Python; deps per `requirements.txt`: `fastapi`, `uvicorn`, `requests`, `sse-starlette>=2.0.0`, `anyio>=4.0.0`
- Data sources: `https://gamma-api.polymarket.com/events?series_slug` and `https://clob.polymarket.com/book`
- PowerShell on Windows — join commands with `;` not `&&`

## Commands
```powershell
pip install -r requirements.txt
pip install pytest                          # dev: 932 tests across 18 files
python -m pytest tests/test_<module>.py -q  # targeted tests (FAST: <1s; ALWAYS prefer during dev & review)
python -m scripts.collect_ticks             # capture: full-depth + tape to run/ticks/ticks_YYYY-MM-DD.jsonl (1s poll, 10 series)
python -m scripts.collect_ticks --once      # single poll smoke test
python -m scripts.verify_tick_data run/ticks # verify tick integrity & data quality
python -m scripts.rebuild_windows           # rebuild oscillation_windows.jsonl + summary from real run/ticks
python -m scripts.backtest run/ticks --offset 0.02 --queue 50  # replay
python -m scripts.sweep_backtest run/ticks --preset grid        # quant parameter sweep (1D: sensitivity; joint: grid; stochastic: random)
python -m uvicorn server.osc_dash:app --host 127.0.0.1 --port 5515  # dashboard
```

## Testing & Fast Iteration Policy
- **Targeted tests ONLY during development and Station IV review:** Run only test files matching modified modules (e.g. `python -m pytest tests/test_backtest_engine.py -q` or `-k <test_name>`). These finish in <1-2s.
- **NEVER run the full test suite (`python -m pytest -q`) locally:** The suite contains 932 tests across 18 files and takes ~96 seconds. Full runs locally are strictly forbidden as redundant.
- **GitHub Actions CI is the sole merge gate:** Pushing a branch automatically triggers full-suite CI in the cloud. Local agents must rely on targeted tests and let CI gate regressions.

## Structure
- `scripts/collect_ticks.py` — primary collector. Polls 10 series (`strategy/series.py:SERIES`), fetches both books + tape via CLOB/data-api, writes replay-grade `run/ticks/ticks_YYYY-MM-DD.jsonl` (full bids/asks + tape_delta per second) + `manifest.json`. Use this for backtests.
- `scripts/verify_tick_data.py` — dataset integrity validator. Verifies JSON syntax, crossed books, timestamp gaps, late starts, mid bounds, and error rates.
- `scripts/sweep_backtest.py` — parameter sweep engine. Runs 1D, 2D, grid, and random quant parameter sweeps across datasets.
- `scripts/rebuild_windows.py` — reconstructs `run/oscillation_windows.jsonl` and `run/oscillation_summary.json` from `run/ticks/*.jsonl` full-depth data.
- `scripts/shadow_ev_pilot.py` — paper-only EV pilot; writes `runs/paper/YYYY-MM-DD_HH-MM_TZ/` directly (`data/` + `research-papers/` + `summary.html` + `manifest.json`).
- `scripts/run_layout.py` — single source of truth for the `runs/` naming + manifest schema (`new_run_dir`/`write_manifest`/`write_summary_html`/`write_paper_stub`).
- `scripts/measure_5m_oscillation.py` — legacy top-of-book collector (best_bid/ask/mid only). Kept for reference; `collect_ticks` is the source of truth for replay.
- `backtest/` — offline replay engine. `engine.py:replay()` is pure (no venue calls), `index.py` builds per-file cid sidecars for slider-speed sweeps.
- `server/osc_dash.py` — FastAPI dashboard on `:5515`. Pages: `/`, `/oscillation`, `/summary`, `/analysis`. APIs: `/api/oscillation`, `/api/goals`, `/api/analysis`, `/api/backtest` (query: offset/queue/pair_cost/quote_lo/quote_hi/exit_*), `/api/ticks/manifest|verify|file|upload-chunk|upload-stream`, collector control `/api/collector/status|start|stop|poll-once`, live execution & cockpit `/api/live/account|state|control|orders|cancel_all|cancel_order|test_order|config|latency|stream`.
- `strategy/` — `series.py` (10-series universe, single source), `markets.py` (book/tape fetchers, `LiveMarket`), `live_trader.py` (order flow & execution engine), `config.py:17` (`MakerConfig`) — heavily commented with hunter-fleet values; most fields are legacy, verify against `README.md:22` before reusing.
- `run/` — gitignored (`.gitignore:6`). Contains `ticks/` (replay-grade) and legacy `oscillation_*.jsonl`. Regenerated; do not commit.
- `runs/` — gitignored (`.gitignore:7`). Self-contained `paper|live` run folders (`runs/paper|live/YYYY-MM-DD_HH-MM_TZ/`); convention in `docs/run-conventions.md`.
- `docs/` — `operations.md` (runbook for capture + replay), `run-conventions.md` (the `/runs` paper|live layout), `live-dashboard-streaming-spec.md` (RTDS & WebSocket live dashboard blueprint), `rtds-clob-latency-audit.md`, `research-spread-bot-conclusions.md` (findings), `backtest-optimization-results.md` (sweep report), `issue-workflow.md` (the global Station pipeline as practiced here) + `git-workflow.md` (branching, commits, PR/CI merge gate), `glossary.md` (agreed names), `engine-decision-rules.md` (what the strategy does), `golden-tick-dataset.md` (the golden dataset — the canonical backtest dataset: definition, collection, certification), `adr/` (why the system is shaped this way); the old `ecc-flow-guide.md` was superseded by the former and removed.
- Other dirs: `tasks/plan.md` + `tasks/todo.md` (working plans).

## Execution Entrypoints & Ownership

| Entrypoint | Status | Owner | Role & Description |
|---|---|---|---|
| `strategy/live_trader.py` (`LiveTraderEngine`) | **Canonical** | Core Strategy & Trading | Sole canonical trading engine for both execution modes (`mode="paper"` and `mode="live"` for real money). Manages order flow, CLOB order execution, 1s tick loops, stop losses, leg chases, and state exposed via `/api/live/*`. |
| `scripts/shadow_ev_pilot.py` | **Canonical** | Strategy Research & Validation | Headless paper EV pilot runner. Runs autonomous validation sessions (e.g. overnight 11h), instantiating `LiveTraderEngine(load_persisted=False)` in paper mode, logging snapshots, and generating self-contained artifact bundles in `runs/paper/`. |
| `server/osc_dash.py` | **Canonical** | Operations & Cockpit | Sole canonical dashboard server (FastAPI on `:5515`). Serves UI tabs and exposes `/api/live/*` endpoints controlling the active `LiveTraderEngine` instance. |
| `scripts/collect_ticks.py` | **Canonical** | Market Data | Sole replay-grade tick data collector. Captures order books and tape deltas for the 10 series to `run/ticks/`. |
| `scripts/backtest.py` & `backtest/engine.py` | **Canonical** | Quantitative Research | Pure offline simulation engine and CLI for replaying tick datasets and running quantitative parameter sweeps. |
| `bot/paper_bot.py` | **Removed** | Legacy | Historical standalone polling script from early exploratory phase. Removed from repository following micro-pilot validation (Issue #148); superseded by `LiveTraderEngine` and `scripts.shadow_ev_pilot`. |
| `ten-bankrolls/` | **Removed** | Legacy | Historical bankroll-farm experiment. Removed from repository following micro-pilot validation (Issue #148); parameter sweeps are now handled by `scripts.sweep_backtest`. |

## Data Model / Classification
- Window classification in `scripts/measure_5m_oscillation.py:125` (`classify_window`): vs base 0.50, `max_up=max(mids)-0.50`, `max_down=0.50-min(mids)`. `oscillating` = both ≥0.02, `monotonic` = one ≥0.02, `flat` = neither. Thresholds at `OSC_THRESH_CENTS=[2.0,3.0]`.
- Dashboard finding (635 windows): median range 49.5¢, 73% oscillating on 5m, `touch_pair` median ~1.01. Exit thresholds unified to 5¢ (0.05) across all series and window durations.

## Gotchas
- **Polymarket Gasless Operations**: Trading (CLOB orders), CTF pair merges (`mergePositions`), and trading approvals (`setupTradingApprovals`, `approveErc20`, `approveErc1155ForAll`) are sponsored (gasless) when routed via the Polymarket Relayer and smart wallet flow per official docs. Direct on-chain EOA transactions incur native gas. External bridging and wallet funding also incur gas.
- **RTDS Crypto Feed & WebSockets**: `prices.crypto.binance` supports `btcusdt`, `ethusdt`, `solusdt`, `xrpusdt` only (no BNB on Binance or Chainlink RTDS feeds; BNB uses REST fallback). CLOB Market WebSocket (`wss://ws-subscriptions-clob.polymarket.com/ws/market`) supports multi-token subscription on a single socket with 10s PING heartbeat and `custom_feature_enabled` for `best_bid_ask`. Use 1s RTDS ticks as leading price signals for faster stop-loss execution.
- `run/` is in `.gitignore`; missing `run/*.jsonl` means collector hasn't run — dashboard shows empty state, not an error.
- Collector uses a pooled `requests.Session` with `(3.05, 5.0)` timeouts (connect, read) and `max_retries=0` — failed markets are skipped for that poll, not retried.
- `strategy/markets.py` sanitizes slugs via `_SAFE_SLUG_RE` before embedding in HTML/DB; `full_book`/`parse_book` tolerates malformed price rows (counted in `malformed`) but raises `ValueError` on structural payload mismatch.
- Context files: `AGENTS.md` (this file) is the canonical project rules; `CLAUDE.md` carries the Claude-facing subset; `CONSTRAINTS.md` and `SPEC.md` are per-issue working files holding the active issue's quality gates and specification — neither is a standing architecture document, and both go stale the moment their issue merges (see §5 of `docs/git-workflow.md`). No `opencode.json` exists.
- Dashboard: `server/osc_dash.py` (FastAPI on :5515) is the sole canonical dashboard.

## GBrain search guidance

This repo is indexed in gbrain as source `crypto-spread`, pinned by `.gbrain-source` in
the repo root, so the commands below route here without a `--source` flag.

**Prefer gbrain over Grep for structural questions** — who calls a symbol, where
it is defined, what it references, what it calls. One query beats opening every
file:

```bash
gbrain code-def <symbol>       # where it is defined
gbrain code-refs <symbol>      # every reference
gbrain code-callers <symbol>   # who calls it
gbrain code-callees <symbol>   # what it calls
gbrain query "<question>"      # semantic search over this repo
```

**Read `status` before trusting an empty result.** `count: 0` means "nothing
found" only when `status` is `ready`. `not_built` or `indexing` means the call
graph is still being built and the empty list proves nothing — fall back to Grep
and say that is what you did.

**Use Grep instead** for literal text, config values, comments, and anything
added since the last sync. The index refreshes on every commit (a `post-commit`
hook) and nightly at 03:00; uncommitted work in progress is not in it. To
refresh now:

```bash
gbrain sync --source crypto-spread --strategy code
```
