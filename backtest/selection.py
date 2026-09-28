"""Dataset selection for backtest replay (issue #308).

Market-series and time-frame selection is a *dataset filter*, not a strategy
parameter: it decides which recorded ticks reach the backtest engine, the same
way `--max-start-delay` does. It therefore lives outside `BacktestParams` and
never touches `params_hash()`.

Filtering happens on raw ticks *before* `group_by_cid()`. Ticks sharing one
`cid` always share `series` and `duration` (one window = one market × one
frame), so a per-tick predicate keeps or drops whole windows — excluded
windows never enter simulation.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional

from strategy.series import SERIES, supported_durations


def parse_series_tokens(raw: Optional[Iterable[str] | str] = None) -> tuple[str, ...]:
    """Normalize a `--series` selection into lowercase substring tokens.

    Accepts a single string, an iterable of strings, or None. Each element is
    split on commas; whitespace is stripped; empties are dropped. An empty
    result means "all series".

    Each token must occur inside at least one canonical SERIES slug
    (case-insensitive), otherwise ValueError naming the token — a typo like
    `bcc` must fail loudly, never replay zero windows silently.
    """
    tokens: list[str] = []
    if raw is None:
        return ()
    items = [raw] if isinstance(raw, str) else list(raw)
    for item in items:
        for part in str(item).split(","):
            tok = part.strip().lower()
            if tok:
                if len(tok) > 64:
                    raise ValueError(
                        f"Series token too long ({len(tok)} chars, max 64): "
                        f"'{tok[:64]}…'."
                    )
                tokens.append(tok)
    # A pathological query (megabytes of tokens) would burn CPU matching
    # every tick; 32 tokens already cover the 10-series universe twice over.
    if len(tokens) > 32:
        raise ValueError(
            f"Too many series tokens ({len(tokens)}, max 32)."
        )
    slugs = [slug.lower() for slug, _dur, _label in SERIES]
    for tok in tokens:
        if not any(tok in slug for slug in slugs):
            raise ValueError(
                f"Unknown series token '{tok}'. Must match part of a known "
                f"series slug, e.g. 'btc', 'eth-up-or-down-5m'."
            )
    return tuple(tokens)


def parse_durations(raw: Optional[Iterable[str | int] | str] = None) -> tuple[int, ...]:
    """Normalize a `--durations` selection into window durations in seconds.

    Accepts comma-separated strings (`"300,900"`), ints, or None. An empty
    result means "all durations". Non-integer or unsupported values raise
    ValueError (validated against `supported_durations()`, not hardcoded).
    """
    values: list[int] = []
    if raw is None:
        return ()
    items = [raw] if isinstance(raw, (str, int)) else list(raw)
    for item in items:
        for part in str(item).split(","):
            part = part.strip()
            if not part:
                continue
            try:
                values.append(int(part))
            except ValueError:
                raise ValueError(
                    f"Invalid duration '{part}'. Must be an integer number of "
                    f"seconds, e.g. 300."
                ) from None
    valid = supported_durations()
    for v in values:
        if v not in valid:
            raise ValueError(
                f"Unsupported duration {v}. Must be one of {list(valid)}."
            )
    return tuple(values)


def _as_int(value) -> Optional[int]:
    """Best-effort int coercion; None when unparseable (never raises)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _pair_matches(
    series_slug: str,
    duration: int,
    series_tokens: tuple[str, ...],
    durations: tuple[int, ...],
) -> bool:
    """True when a (series, duration) pair survives the selection."""
    if series_tokens:
        hay = str(series_slug or "").lower()
        if not any(tok in hay for tok in series_tokens):
            return False
    # Tolerant compare: a malformed (non-integer) duration label matches
    # nothing instead of crashing the slice.
    if durations and _as_int(duration) not in durations:
        return False
    return True


def tick_matches(
    tick: dict,
    series_tokens: tuple[str, ...] = (),
    durations: tuple[int, ...] = (),
) -> bool:
    """True when one raw tick survives the selection.

    Matches a token against the tick's `series` or `slug` field (lowercased),
    mirroring the substring style of `BacktestParams.exit_thresh()`.
    """
    if series_tokens:
        hay = f"{tick.get('series') or ''} {tick.get('slug') or ''}".lower()
        if not any(tok in hay for tok in series_tokens):
            return False
    if durations and _as_int(tick.get("duration")) not in durations:
        return False
    return True


def apply_selection(
    snaps: Iterable[dict],
    series_tokens: tuple[str, ...] = (),
    durations: tuple[int, ...] = (),
) -> list[dict] | Iterable[dict]:
    """Filter raw ticks to the selection; empty selection returns input unchanged."""
    st = tuple(series_tokens or ())
    du = tuple(durations or ())
    if not st and not du:
        return snaps
    return [s for s in snaps if tick_matches(s, st, du)]


def found_pairs(grouped: Iterable[tuple[str, list[dict]]]) -> dict[tuple[str, int], int]:
    """Count windows per (series, duration) pair from grouped windows."""
    out: dict[tuple[str, int], int] = {}
    for _cid, group in grouped:
        if not group:
            continue
        key = (group[0].get("series", ""), _as_int(group[0].get("duration")) or 0)
        out[key] = out.get(key, 0) + 1
    return out


def found_pairs_from_windows(windows: Iterable[dict]) -> dict[tuple[str, int], int]:
    """Count windows per (series, duration) pair from replay `per_window` rows.

    Lets callers that already replayed (CLI) build coverage without grouping
    the tick stream a second time.
    """
    out: dict[tuple[str, int], int] = {}
    for w in windows:
        key = (w.get("series", ""), _as_int(w.get("duration")) or 0)
        out[key] = out.get(key, 0) + 1
    return out


def find_golden_manifest(source: str | Path) -> Optional[Path]:
    """Locate the golden manifest adjacent to a tick source, if any.

    Directory source → `<dir>/golden_manifest.json`; file source →
    `<parent>/golden_manifest.json`. Returns None when absent or unreadable —
    non-golden sources (e.g. `run/ticks/` with a collector `manifest.json` of
    a different schema) simply fall back to the canonical SERIES universe.
    """
    path = Path(source)
    candidate = path / "golden_manifest.json" if path.is_dir() else path.parent / "golden_manifest.json"
    try:
        if candidate.is_file():
            return candidate
    except OSError:
        return None
    return None


def expected_pairs(
    source: str | Path,
    series_tokens: tuple[str, ...] = (),
    durations: tuple[int, ...] = (),
) -> tuple[set[tuple[str, int]], dict[tuple[str, int], int], str]:
    """(series, duration) pairs the selected slice should contain, and origin.

    Returns (pairs, windows_per_pair, origin). Reads `days[]...market_breakdown[]`
    (`windows > 0`) from the adjacent golden manifest when present — a file
    source is restricted to the `days[]` entry whose `file` matches the source
    basename. Without a manifest, falls back to the canonical SERIES universe
    (window counts unknown → empty map). The selection predicate applies to
    expected pairs too, so a BTC-only selection expects two pairs.
    """
    st = tuple(series_tokens or ())
    du = tuple(durations or ())
    manifest = find_golden_manifest(source)
    if manifest is not None:
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict):
            days = data.get("days", [])
            path = Path(source)
            if path.is_file():
                days = [d for d in days if d.get("file") == path.name]
            pairs: set[tuple[str, int]] = set()
            windows: dict[tuple[str, int], int] = {}
            for day in days:
                for entry in day.get("market_breakdown", []):
                    if entry.get("windows", 0) <= 0:
                        continue
                    key = (entry.get("series", ""), entry.get("duration", 0))
                    pairs.add(key)
                    windows[key] = windows.get(key, 0) + entry.get("windows", 0)
            kept = {p for p in pairs if _pair_matches(p[0], p[1], st, du)}
            return kept, {p: windows[p] for p in kept}, "golden_manifest"
    canonical = {(slug, dur) for slug, dur, _label in SERIES}
    kept = {p for p in canonical if _pair_matches(p[0], p[1], st, du)}
    return kept, {}, "canonical"


def build_coverage(
    source: str | Path,
    found: dict[tuple[str, int], int],
    series_tokens: tuple[str, ...] = (),
    durations: tuple[int, ...] = (),
) -> dict:
    """Completeness report: pairs found in the slice vs pairs expected.

    `found` maps (series, duration) → window counts — from `found_pairs()`
    over grouped windows, or from `found_pairs_from_windows()` over replay
    `per_window` rows (no second grouping pass). Per-pair window counts are
    informational — date filters, start-delay filters, and midnight-crossing
    windows can change them. Pair counts are the completeness check.
    Single-file sources are informational only for the same reason (a
    window's ticks may straddle two daily files).
    """
    st = tuple(series_tokens or ())
    du = tuple(durations or ())
    found = dict(found or {})
    expected, expected_windows, origin = expected_pairs(source, st, du)
    found_set = set(found)
    return {
        "filtered": bool(st or du),
        "selection": {"series": list(st), "durations": list(du)},
        "pairs_found": len(found_set),
        "pairs_expected": len(expected),
        "missing_pairs": sorted(f"{s}@{d}" for s, d in (expected - found_set)),
        "windows_found": {f"{s}@{d}": n for (s, d), n in sorted(found.items())},
        "windows_expected": {f"{s}@{d}": n for (s, d), n in sorted(expected_windows.items())},
        "expected_source": origin,
    }
