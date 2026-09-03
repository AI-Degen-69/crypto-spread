# SPEC: Part 1 — Market Selection Engine and CLI Filtering

## Objective
Enable operators and trading scripts to select any combination of cryptocurrency assets (`BTC`, `ETH`, `BNB`, `SOL`, `XRP`) and window durations (`5m`, `15m`, or `both`), rather than being locked to only the 5m markets.

## Tech Stack
- Python 3.10+
- `fastapi`, `uvicorn`, `requests`, `pytest`
- No external DB; in-memory engine state

## Commands
- Run tests: `python -m pytest -q`
- Run single window test (5m): `python -m scripts.run_single_window_test --series btc-up-or-down-5m`
- Run single window test (15m): `python -m scripts.run_single_window_test --tokens BTC --duration 15m`
- Verify docstrings: `python -m pytest tests/test_docstrings.py`

## Project Structure
- `strategy/series.py`: Series universe and filtering helpers (`filter_series`, `token_for_slug`).
- `strategy/streaming.py`: Spot tick symbol mappings (`SYMBOL_TO_SERIES`, `SERIES_TO_SYMBOL`, `series_for_symbol`).
- `strategy/live_trader.py`: Multi-market selection, 15m metadata, and spot tick dispatch.
- `scripts/run_single_window_test.py`: CLI flags for `--tokens` and `--duration`.
- `tests/test_series.py` & `tests/test_live_trader.py` & `tests/test_streaming.py`: Unit tests.

## Code Style & Architecture
- **Pure Universe Filter**:
```python
def filter_series(
    tokens: Optional[Iterable[str]] = None,
    durations: Optional[Iterable[int]] = None,
) -> tuple[tuple[str, int, str], ...]:
    """Filter SERIES universe by token symbols and window durations."""
    ...
```
- **Docstring Coverage**: Every non-test function and class must have a non-empty docstring to satisfy `tests/test_docstrings.py`.
- **Snapshot Safety**: In `LiveTraderEngine._tick_all_markets`, take a single `list(self.markets.items())` snapshot to avoid race conditions during tick iteration.

## Testing Strategy
- `tests/test_series.py`: Test `filter_series` with all permutations and verify `ValueError` on invalid inputs.
- `tests/test_streaming.py`: Test `series_for_symbol` and spot symbol aliases.
- `tests/test_live_trader.py`: Test `LiveTraderEngine` initialization with custom subsets, dynamic reconfiguration via `update_config`, order cleanup on removal, 15m 10% timeout (90s), and multi-market spot fan-out.
- Regression testing: Verify all existing tests pass (`python -m pytest -q`).

## Boundaries
- **Always**:
  - Maintain backward compatibility: `LiveTraderEngine()` defaults to 5m markets if no selection is passed.
  - Single source of truth in `strategy/series.py:SERIES`.
  - Validate and normalize token symbols and durations.
  - Cancel open/resting orders before removing deselected markets.
- **Ask first**:
  - Modifying live order execution endpoints or order signing logic.
- **Never**:
  - Leave orphaned resting orders when a market is deselected at runtime.
  - Create functions without docstrings.

## Success Criteria
1. `filter_series(tokens=["BTC", "ETH"], durations=[300, 900])` returns 4 series (BTC 5m, BTC 15m, ETH 5m, ETH 15m).
2. `filter_series()` raises `ValueError` on invalid token or duration.
3. `LiveTraderEngine(tokens=["SOL"], durations=[900])` initializes only `sol-up-or-down-15m`.
4. `engine.update_config(selected_markets=["btc-up-or-down-5m", "btc-up-or-down-15m"])` reconfigures active markets dynamically, safely cancelling removed orders.
5. Spot tick on `btcusdt` fans out and updates both `btc-up-or-down-5m` and `btc-up-or-down-15m` when both are active.
6. `scripts/run_single_window_test.py` supports `--tokens` and `--duration` flags.
7. All unit and docstring tests pass with `python -m pytest -q`.
