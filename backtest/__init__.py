"""backtest package — SPREAD-2 replay engine."""
from .engine import (
    BacktestParams,
    WindowResult,
    iter_ticks,
    group_by_cid,
    load_ticks,
    replay,
)
from .index import (
    IndexEntry,
    build_index,
    load_index,
    group_by_cid_indexed,
    is_fresh,
)
