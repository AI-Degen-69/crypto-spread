"""Series universe for the SPREAD-2 lab.

Single source of truth for which 5m/15m markets the collector polls and the
backtest replays. Both `scripts/measure_5m_oscillation.py` and
`scripts/collect_ticks.py` import SERIES from here so the universe cannot
drift between capture and replay (Plan D5).
"""
from __future__ import annotations
from typing import Final, Iterable, Optional

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


def token_for_slug(slug: str) -> str:
    """Extract normalized uppercase token symbol (e.g. BTC) from a series slug."""
    if "-up-or-down-" in slug:
        return slug.split("-up-or-down-")[0].upper()
    return slug.split("-")[0].upper()


def supported_tokens() -> tuple[str, ...]:
    """Return tuple of supported uppercase token symbols in canonical order."""
    seen: list[str] = []
    for s in SERIES:
        tok = token_for_slug(s[0])
        if tok not in seen:
            seen.append(tok)
    return tuple(seen)


def supported_durations() -> tuple[int, ...]:
    """Return tuple of supported window durations in seconds in canonical order."""
    seen: list[int] = []
    for s in SERIES:
        dur = s[1]
        if dur not in seen:
            seen.append(dur)
    return tuple(seen)


def filter_series(
    tokens: Optional[Iterable[str]] = None,
    durations: Optional[Iterable[int]] = None,
) -> tuple[tuple[str, int, str], ...]:
    """Filter series universe by token symbols and window durations.

    Args:
        tokens: Iterable of token symbols (e.g. 'BTC', 'eth'). Case-insensitive.
            Treats None or empty as all tokens.
        durations: Iterable of window durations in seconds (e.g. 300, 900).
            Treats None or empty as all durations.

    Returns:
        Subset of SERIES matching the filters in original order.

    Raises:
        ValueError: If an unknown token or unsupported duration is supplied.
    """
    valid_tokens = supported_tokens()
    valid_durations = supported_durations()

    if tokens is not None:
        tokens_list = []
        for t in tokens:
            if not isinstance(t, str) or not t.strip():
                raise ValueError(f"Invalid empty token: '{t}'")
            tok = t.strip().upper()
            if tok not in valid_tokens:
                raise ValueError(f"Unsupported token '{tok}'. Must be one of {valid_tokens}")
            tokens_list.append(tok)
        if tokens_list:
            tok_set = set(tokens_list)
            filtered = [s for s in SERIES if token_for_slug(s[0]) in tok_set]
        else:
            filtered = list(SERIES)
    else:
        filtered = list(SERIES)

    durations_list = list(durations) if durations is not None else []
    if durations_list:
        for d in durations_list:
            if d not in valid_durations:
                raise ValueError(f"Unsupported duration {d}. Must be one of {valid_durations}")
        dur_set = set(durations_list)
        filtered = [s for s in filtered if s[1] in dur_set]

    return tuple(filtered)
