# AGENTS.md — crypto-spread

Independent lab for 5m/15m SPREAD-2 capture on BTC/ETH/BNB/SOL/XRP. Isolated from `spread-hunter` (original rewards bot). No tests, lint, CI, or build step.

## Stack
- Python, `fastapi` + `uvicorn` + `requests` only (`requirements.txt:3`)
- Data sources: `https://gamma-api.polymarket.com/events?series_slug` and `https://clob.polymarket.com/book`
- PowerShell on Windows — join commands with `;` not `&&`

## Commands
```powershell
pip install -r requirements.txt
python -m scripts.measure_5m_oscillation                # continuous: polls 10 series every 1s
python -m scripts.measure_5m_oscillation --once         # single poll (smoke test)
python -m scripts.measure_5m_oscillation --windows 20   # run until 20 windows close then exit
python -m uvicorn server.osc_dash:app --host 127.0.0.1 --port 8802  # dashboard
```

## Structure
- `scripts/measure_5m_oscillation.py` — only collector. Polls all 10 series (`SERIES` list, line 38), fetches live market via gamma-api + both books via CLOB, computes `mid=(bid+ask)/2` from UP book, `touch_pair=up_ask+down_ask`, `resting_pair=0.96` (SPREAD_OFFSET=0.02). Writes 3 files to `run/`.
- `server/osc_dash.py` — FastAPI dashboard on `:8802`. Reads `run/oscillation_*.jsonl` + `run/oscillation_summary.json`. Routes: `/` + `/oscillation` (main table), `/summary` + `/analysis` (charts), `/api/oscillation`, `/api/goals`, `/api/analysis`.
- `strategy/` — copied from `spread-hunter`. `config.py:17` (`MakerConfig`) and `markets.py` (book/tape fetchers, `LiveMarket`) are the real implementations. Heavily commented with measured values from the hunter fleet — most `MakerConfig` fields (rewards, skew, caps) are legacy from that experiment, not the SPREAD-2 target described in README. Verify against `README.md:22` (mid-2¢, queue 50, pair_cost <0.995) before reusing.
- `run/` — gitignored (`.gitignore:6`). Contains `oscillation_snapshots.jsonl` (per-second raw), `oscillation_windows.jsonl` (one line per closed window), `oscillation_summary.json` (aggregated per-series). Regenerated on every run; do not commit.
- `docs/` — empty placeholder.

## Data Model / Classification
- Window classification in `scripts/measure_5m_oscillation.py:125` (`classify_window`): vs base 0.50, `max_up=max(mids)-0.50`, `max_down=0.50-min(mids)`. `oscillating` = both ≥0.02, `monotonic` = one ≥0.02, `flat` = neither. Thresholds at `OSC_THRESH_CENTS=[2.0,3.0]`.
- Dashboard finding (635 windows): median range 49.5¢, 73% oscillating on 5m, `touch_pair` median ~1.01. Exit thresholds in dashboard are derived stats (BTC 5m +9¢, SOL +11¢, others +12¢, 15m +13¢) — not enforced in collector code.

## Gotchas
- `run/` is in `.gitignore`; missing `run/*.jsonl` means collector hasn't run — dashboard shows empty state, not an error.
- Collector uses a pooled `requests.Session` with `(3.05, 5.0)` timeouts (connect, read) and `max_retries=0` — failed markets are skipped for that poll, not retried.
- `strategy/markets.py` sanitizes slugs via `_SAFE_SLUG_RE` before embedding in HTML/DB; `full_book`/`parse_book` tolerates malformed price rows (counted in `malformed`) but raises `ValueError` on structural payload mismatch.
- No `opencode.json`, `CLAUDE.md`, tests, or CI workflows exist — no hidden verification steps to run.
- Sister repo at `C:\Users\Tiger\Agents\Projects\AI Trading\spread-hunter` is the original bot; keep the two isolated per `README.md:24`.
