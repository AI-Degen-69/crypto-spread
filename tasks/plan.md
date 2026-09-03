# Technical Implementation Plan: Market Selection Engine & CLI Filtering (Issue #50)

## Overview
Implement universe filtering in `strategy/series.py`, integrate market selection into `LiveTraderEngine` and `strategy/streaming.py`, expose CLI flags on `scripts/run_single_window_test.py`, and add comprehensive unit tests.

## Dependency Graph
1. `strategy/series.py` (`filter_series`, `token_for_slug`) [No dependencies]
2. `strategy/streaming.py` (`series_for_symbol`, 15m aliases) [Depends on 1]
3. `strategy/live_trader.py` (Engine market selection, dynamic `update_config`, order cleanup, spot fan-out, snapshot safety) [Depends on 1, 2]
4. `scripts/run_single_window_test.py` (CLI flags `--tokens`, `--duration`) [Depends on 1, 3]
5. Tests (`tests/test_series.py`, `tests/test_streaming.py`, `tests/test_live_trader.py`) [Depends on 1, 2, 3, 4]

## Tasks Breakdown
See [tasks/todo.md](file:///c:/Users/Tiger/Agents/Projects/AI%20Trading/crypto-spread/tasks/todo.md) for granular task items.
