"""Series universe for the SPREAD-2 lab.

Single source of truth for which 5m/15m markets the collector polls and the
backtest replays. Both `scripts/measure_5m_oscillation.py` and
`scripts/collect_ticks.py` import SERIES from here so the universe cannot
drift between capture and replay (Plan D5).
"""
from __future__ import annotations
from typing import Final

SERIES: Final[tuple[tuple[str, int, str], ...]] = (
    ("btc-up-or-down-5m",  300,  "BTC 5m"),
    ("eth-up-or-down-5m",  300,  "ETH 5m"),
    ("bnb-up-or-down-5m",  300,  "BNB 5m"),
    ("sol-up-or-down-5m",  300,  "SOL 5m"),
    ("xrp-up-or-down-5m",  300,  "XRP 5m"),
    ("btc-up-or-down-15m", 900,  "BTC 15m"),
    ("eth-up-or-down-15m", 900,  "ETH 15m"),
    ("bnb-up-or-down-15m", 900,  "BNB 15m"),
    ("sol-up-or-down-15m", 900,  "SOL 15m"),
    ("xrp-up-or-down-15m", 900,  "XRP 15m"),
)


def slugs() -> tuple[str, ...]:
    """Series slugs only — for the collector loop and backtest index."""
    return tuple(slug for slug, _dur, _label in SERIES)


def by_duration(seconds: int) -> tuple[tuple[str, int, str], ...]:
    """Return the subset of SERIES with the given window duration."""
    return tuple(s for s in SERIES if s[1] == seconds)
