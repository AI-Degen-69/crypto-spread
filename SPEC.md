# SPEC.md — Issue #364: Pair Cost & Merge Edge Visibility

## 1. Overview & Goals
When replaying SPREAD-2 backtests, a 100% `pair_captured` rate can co-exist with a large net loss because leg-chasing fills legs at different timestamps when market mids have drifted. Currently, `trades_sample` rows and aggregate endpoints display entry prices from only the *first* entry of a window alongside whole-window P&L, making multi-event windows arithmetically un-reconcilable and misleadingly implying that captured pairs were profitable.

This specification details the pair-economics telemetry additions required to make pair cost and merge edge visible and reconcilable across backtest engine outputs and dashboard API streams without altering existing field definitions or trading logic.

## 2. Shared Math Contract (`strategy/book_math.py`)
Add a single source-of-truth helper function for realized pair edge:

```python
def realized_pair_edge_cents(
    entry_up: float,
    entry_down: float,
    merge_gas_usd: float = 0.0,
    quote_shares: int = 50,
) -> float:
    """Computes net pair edge in cents per share after deducting amortized merge gas.

    Formula:
      (1.00 - (entry_up + entry_down)) * 100.0 - (merge_gas_usd * 100.0) / max(1, quote_shares)
    """
```

## 3. Data Model & Engine Schema (`backtest/engine.py`)

### `WindowResult` New Fields
- `first_pair_cost: float | None = None` — Combined resting price (`entry_up + entry_down`) of the first completed pair in the window (in dollars).
- `mean_pair_edge_cents: float | None = None` — Average realized pair edge (in cents) across all pair completions in the window.
- `worst_pair_edge_cents: float | None = None` — Worst (lowest) realized pair edge (in cents) among pair completions in the window.
- `pair_pnl_cents: float = 0.0` — Total net P&L (in cents) contributed by all pair completions in the window.

### `trades_sample` Output Schema
Both `backtest/engine.py:replay()` and `server/osc_dash.py:/api/backtest` will include in each `trades_sample` row:
- `first_pair_cost`: `float | None`
- `mean_pair_edge_cents`: `float | None`
- `worst_pair_edge_cents`: `float | None`
- `pair_pnl_cents`: `float`
- `pairs_count`: `int`
- `stops_count`: `int`

### Aggregate Summary Schema (`overall`, `per_series`, `per_duration`)
Both engine and dashboard summary outputs will contain:
- `pair_rate`: `float` (unchanged denominator: `total_pairs / total_windows`)
- `pair_rate_entered`: `float` (`total_pairs / entered_windows`, `0.0` if `entered_windows == 0`)
- `mean_pair_cost`: `float | None` (average `first_pair_cost` across windows with pairs)
- `mean_pair_edge_cents`: `float | None` (average `mean_pair_edge_cents` across windows with pairs)
- `total_pair_pnl_cents`: `float` (sum of `pair_pnl_cents` across windows)

## 4. Verification & Non-Regression
- Unit tests verify `realized_pair_edge_cents` reproduces engine `pnl_cents` for single-merge windows.
- Parity tests confirm `backtest/engine.py` and `server/osc_dash.py` produce identical aggregate numbers on synthetic fixtures.
- Existing CLI output in `scripts/backtest.py` remains unchanged.
