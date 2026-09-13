"""Full-depth tick collector for 5m/15m SPREAD-2 replay.

Fork of `scripts/measure_5m_oscillation.py:1-305`. Same gamma discovery, but
every snapshot persists the complete UP and DOWN order books plus the
trade-tape delta. Output is replay-grade jsonl that `backtest.engine.replay`
consumes offline.

Cadence — read this before treating the output as a 1-second series (#167).
A round is one pass over the whole slate, and the collector then sleeps
POLL_INTERVAL, so the real gap between snapshots is round + POLL_INTERVAL.
On this hardware a warm 10-series round measures ~450ms (median of 6), i.e.
~1.45s between snapshots. It was ~2.7s (so ~3.8s between snapshots) before the
slate was fanned out.
The live figure is published every tick as `sampling_interval_s` in
manifest.json — use that, not POLL_INTERVAL, when reasoning about granularity.
The opening round is several times slower (empty gamma cache, cold TLS pool,
socket still connecting) and is reported as `tick_ms_first` rather than
charged against TICK_BUDGET_MS.

Per-series failure is isolated (D2, D4):
- The slate is fetched concurrently over a bounded pool, with a per-worker
  start stagger (SERIES_STAGGER_SEC) so the round ramps instead of firing as
  one synchronized burst — that burst is what triggers venue 429s at window
  boundaries.
- Per-cid tape dedup set is dropped when the window closes — bounded memory.
- `err` field on a snap means "this series failed this second", other 9
  series still write normally.

Usage:
  python -m scripts.collect_ticks                  # continuous
  python -m scripts.collect_ticks --once           # one poll (smoke test)
  python -m scripts.collect_ticks --days 1         # stop after 1 day boundary
  python -m scripts.collect_ticks --out run/ticks  # override output dir
  python -m scripts.collect_ticks --gzip           # rotate as .jsonl.gz
"""
from __future__ import annotations
import argparse
import json
import random
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

import requests
from strategy import book_math
from strategy.markets import full_book, recent_trades
from strategy.series import SERIES
from strategy.windows import compute_summary, finalize_window, write_json_atomic

if TYPE_CHECKING:  # import only for typing: the bridge is loaded lazily below
    from strategy.streaming import CLOBStreamCollectorBridge


def ensure_utf8_streams() -> None:
    """Force UTF-8 console streams so Unicode output cannot crash under cp1252."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


ensure_utf8_streams()

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "run" / "ticks"
DEFAULT_OUT.mkdir(parents=True, exist_ok=True)

GAMMA_HOST = "https://gamma-api.polymarket.com"
CLOB_HOST = "https://clob.polymarket.com"
TRADES_API = "https://data-api.polymarket.com/trades"

POLL_INTERVAL = 1.0
JITTER_SEC = 0.010

# The slate is polled concurrently (issue #167): ten independent series cost
# ~269ms each and adding them up put every round over TICK_BUDGET_MS. One
# worker per series, created once for the process, never per tick.
MAX_POLL_WORKERS = len(SERIES)
# Fan-out would otherwise fire the whole slate as a single burst, which is
# exactly what the old per-request jitter existed to prevent (docstring D2/D4).
# Worker i waits i * this before its first request, so the requests ramp.
SERIES_STAGGER_SEC = 0.010
SPREAD_OFFSET = 0.02
TAPE_LIMIT = 200
# Measured warm rounds after #167: 442 / 453 / 475 / 399 / 425 / 457 ms.
# 1500ms is ~3x that ceiling — loose enough that ordinary venue latency is not
# reported as a fault, tight enough that losing the fan-out (~2.7s) trips it.
# Lowered from 2000ms, which the pre-#167 round exceeded on literally every
# tick and so reported nothing at all.
TICK_BUDGET_MS = 1500.0

# The opening round is legitimately several times slower — empty gamma cache,
# cold TLS pool, socket still connecting — and measured 3.1s warm-process /
# 5.8s cold-process. It gets its own, looser ceiling rather than no ceiling at
# all: with per-request timeouts of (3.05, 5.0) a wedged DNS or a partial venue
# outage at boot can stretch a cold round to tens of seconds, and without this
# it would be recorded and never reported, which is the same blind spot #167
# exists to close. Reported as `slow_first_tick` so it stays distinguishable
# from a steady-state degradation.
COLD_TICK_BUDGET_MS = 15000.0

# Cross-source tape dedup (issue #165). The socket prints a trade the instant it
# happens; the REST tape reports the same trade for as long as it stays in the
# last-200 window, and its dedup set never saw the socket's copy. So every level
# the socket printed is remembered for this long and suppressed on the REST
# fallback path. Suppression can only under-count volume at a level we already
# printed — the conservative direction, and fill detection is presence-based.
WS_REST_DEDUP_TTL = 60.0

# When the socket is authoritative for a leg, the REST tape call is pure latency
# (~97ms per series per tick) spent to confirm what the socket already reported.
# "Authoritative" is deliberately conservative and needs all three of:
#   - the bridge reports connected (the #165 PONG watchdog already drops a
#     half-open socket, so this is a real liveness signal, not a hopeful one);
#   - the token has been continuously subscribed for WS_TOKEN_WARMUP, which is
#     what makes silence mean "no trades" rather than "not listening yet" —
#     a new window's tokens only join the subscription at the END of the tick
#     that opened it, and a reconnect restarts this clock because prints during
#     the gap are simply gone;
#   - the socket has printed for that token within WS_AUTHORITY_HORIZON, so a
#     subscription that breaks without dropping the connection still gets
#     cross-checked against REST instead of silently starving the tape.
WS_TOKEN_WARMUP = 5.0
WS_AUTHORITY_HORIZON = 90.0

def assert_unique_series_slugs() -> None:
    """Fail loudly at import if two series share a slug.

    The poll fan-out writes `_gamma_cache` from worker threads without a lock,
    and that is only safe because each worker owns a distinct slug key. A
    duplicate slug would turn a config typo into a silent cross-thread
    overwrite, so it is rejected here rather than discovered in the data.
    """
    slugs = [slug for slug, _dur, _label in SERIES]
    dupes = sorted({s for s in slugs if slugs.count(s) > 1})
    if dupes:
        raise ValueError(f"duplicate series slugs would race the gamma cache: {dupes}")


assert_unique_series_slugs()


# Per-cid state: { cid: {series, slug, start_ts, end_ts, up_token, down_token,
#                         seen_tape, seen_ws, ws_levels, ws_ready_at,
#                         ws_last_print, snap_count, label, duration, mids,
#                         touch_pairs} }
# Mutated only on the main thread — the poll workers return values, never write.
windows: dict[str, dict[str, Any]] = {}


def run_dir_for(out_dir: Path) -> Path:
    """Resolve the dataset dir holding oscillation_windows.jsonl for a ticks out_dir."""
    out_dir = Path(out_dir)
    if out_dir.name == "ticks":
        return out_dir.parent
    return out_dir


def write_window(rec: dict[str, Any], out_dir: Path) -> None:
    """Append one closed-window record to <out_dir>/oscillation_windows.jsonl."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "oscillation_windows.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def refresh_summary(out_dir: Path) -> None:
    """Recompute oscillation_summary.json from <out_dir>/oscillation_windows.jsonl."""
    out_dir = Path(out_dir)
    rows: list[dict[str, Any]] = []
    f = out_dir / "oscillation_windows.jsonl"
    if f.exists():
        with open(f, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    write_json_atomic(out_dir / "oscillation_summary.json", compute_summary(rows))


_poll_executor: Optional[ThreadPoolExecutor] = None


def get_poll_executor() -> ThreadPoolExecutor:
    """The one bounded pool the tick loop fans out over; built on first use."""
    global _poll_executor
    if _poll_executor is None:
        _poll_executor = ThreadPoolExecutor(
            max_workers=MAX_POLL_WORKERS, thread_name_prefix="collect-tick")
    return _poll_executor


def shutdown_poll_executor() -> None:
    """Release the pool so a restarted collector starts from a clean one."""
    global _poll_executor
    if _poll_executor is not None:
        _poll_executor.shutdown(wait=False)
        _poll_executor = None


SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0"})
SESSION.mount("https://", requests.adapters.HTTPAdapter(
    pool_connections=MAX_POLL_WORKERS, pool_maxsize=MAX_POLL_WORKERS * 2,
    max_retries=0))
SESSION.mount("http://", requests.adapters.HTTPAdapter(
    pool_connections=4, pool_maxsize=4, max_retries=0))


def iso_to_unix(s: str) -> float:
    """Convert ISO timestamp string to Unix epoch seconds."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s).timestamp()


def fetch_live_for_series(series_slug: str):
    """Fetch active live market metadata for a series slug from Gamma API."""
    try:
        r = SESSION.get(
            f"{GAMMA_HOST}/events",
            params={"series_slug": series_slug, "closed": "false", "limit": 500},
            timeout=(3.05, 5.0),
        )
        r.raise_for_status()
        events = r.json()
    except Exception as e:
        return None, f"gamma err {e}"
    now = time.time()
    candidates = []
    for ev in events:
        for m in ev.get("markets") or []:
            try:
                raw = m.get("clobTokenIds")
                tids = json.loads(raw) if isinstance(raw, str) else raw
                if not tids or len(tids) != 2:
                    continue
                st = iso_to_unix(m.get("eventStartTime"))
                et = iso_to_unix(m.get("endDate") or m.get("endDateIso"))
                if st <= now < et:
                    candidates.append((st, et, m))
            except Exception:
                continue
    if not candidates:
        return None, "no live"
    candidates.sort(key=lambda x: x[0], reverse=True)
    st, et, m = candidates[0]
    raw = m.get("clobTokenIds")
    tids = json.loads(raw) if isinstance(raw, str) else raw
    return {
        "conditionId": m["conditionId"],
        "slug": m["slug"],
        "start_ts": st, "end_ts": et,
        "up_token": str(tids[0]), "down_token": str(tids[1]),
        "series": series_slug,
    }, None


# Gamma re-resolves a market whose conditionId, tokens and end_ts cannot change
# until the window rolls, so the lookup was repeated ~390ms per tick for an
# answer that was already known (issue #167). The cache is bounded in time, not
# just by end_ts: a market replaced or cancelled mid-window is still picked up
# within GAMMA_CACHE_MAX_AGE instead of being pinned for the whole window.
GAMMA_CACHE_MAX_AGE = 30.0

# { series_slug: (resolved_at_ts, market_info) }
_gamma_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def reset_gamma_cache() -> None:
    """Drop every cached market resolution (process restart / test isolation)."""
    _gamma_cache.clear()


def resolve_series_market(series_slug: str, now: Optional[float] = None
                          ) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """`fetch_live_for_series` behind a per-window cache. Same (info, err) shape.

    A failure is never cached: gamma erroring once must not blind the collector
    to that series until the entry would have expired anyway.
    """
    if now is None:
        now = time.time()
    cached = _gamma_cache.get(series_slug)
    if cached is not None:
        resolved_at, info = cached
        if now < info["end_ts"] and (now - resolved_at) < GAMMA_CACHE_MAX_AGE:
            return info, None
    info, err = fetch_live_for_series(series_slug)
    if info is None:
        return None, err
    _gamma_cache[series_slug] = (now, info)
    return info, None


def write_snap(line: dict, out_dir: Path, day_key: str, gzip: bool) -> str:
    """Append one tick line to run/ticks/ticks_<day>.jsonl[.gz]; returns path."""
    suffix = ".jsonl.gz" if gzip else ".jsonl"
    path = out_dir / f"ticks_{day_key}{suffix}"
    payload = (json.dumps(line) + "\n").encode("utf-8")
    if gzip:
        import gzip as _gzip
        with open(path, "ab") as f:
            f.write(_gzip.compress(payload))
    else:
        with open(path, "ab") as f:
            f.write(payload)
    return str(path)


TAPE_ROLLING_WINDOW_SECS = 300.0  # 5-minute rolling window for tape silence alerting
TAPE_ALERT_THRESHOLD = 0.99       # silence threshold (>99% empty trades)


def update_manifest(out_dir: Path, stats: dict) -> None:
    """Persist public collector stats to manifest.json (excluding internal keys)."""
    data = {k: v for k, v in stats.items() if not k.startswith("_")}
    data["ts"] = time.time()
    (out_dir / "manifest.json").write_text(
        json.dumps(data, indent=2),
        encoding="utf-8",
    )


def record_tape_sample(stats: dict, now: float, has_trades: bool, num_entries: int) -> None:
    """Record tape sample into stats, updating aggregate metrics and rolling 5m alert state."""
    if has_trades:
        stats["tape_non_empty_count"] = stats.get("tape_non_empty_count", 0) + 1
        stats["tape_entries_total"] = stats.get("tape_entries_total", 0) + num_entries
    else:
        stats["tape_empty_count"] = stats.get("tape_empty_count", 0) + 1

    total_tape_checks = stats.get("tape_empty_count", 0) + stats.get("tape_non_empty_count", 0)
    if total_tape_checks > 0:
        stats["tape_empty_rate"] = round(stats.get("tape_empty_count", 0) / total_tape_checks, 4)

    # Rolling 5-minute window for alert
    tape_window = stats.setdefault("_tape_window", [])
    tape_window.append({"ts": now, "empty": not has_trades})
    cutoff = now - TAPE_ROLLING_WINDOW_SECS
    while tape_window and tape_window[0]["ts"] < cutoff:
        tape_window.pop(0)

    w_tot = len(tape_window)
    w_empty = sum(1 for item in tape_window if item["empty"])
    w_span = (tape_window[-1]["ts"] - tape_window[0]["ts"]) if w_tot >= 2 else 0.0
    recent_rate = round(w_empty / w_tot, 4) if w_tot > 0 else 0.0
    stats["tape_recent_empty_rate"] = recent_rate
    # Alert requires rolling duration of at least 5m (with 10s tolerance for tick scheduling jitter)
    stats["tape_alert"] = bool(
        w_span >= (TAPE_ROLLING_WINDOW_SECS - 10.0) and recent_rate > TAPE_ALERT_THRESHOLD
    )


# Issue #170: local copies of these disagreed with the backtest engine and the
# live trader on one-sided and empty books. `strategy/book_math` is now the
# single definition, so a recorded tick and a live tick are read the same way.
compute_mid = book_math.mid
queue_ahead = book_math.queue_ahead


def now_day_key() -> str:
    """Return current UTC date formatted as YYYY-MM-DD."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def drain_ws_tape(ws_bridge: "CLOBStreamCollectorBridge", w: dict[str, Any],
                  now: float) -> list[dict]:
    """Drain streamed prints for one window's two tokens into tape rows.

    Deduplicated by full print identity (`asset:price:size:ts:hash`) so a
    redelivered frame is written once, and every drained level is stamped into
    `w["ws_levels"]` so the REST fallback does not echo it back.
    """
    seen_ws: set = w.setdefault("seen_ws", set())
    ws_levels: dict = w.setdefault("ws_levels", {})
    last_print: dict = w.setdefault("ws_last_print", {})
    rows: list[dict] = []
    for tok in (w["up_token"], w["down_token"]):
        for t in ws_bridge.drain_trades_for_token(tok) or []:
            try:
                price = float(t["price"])
                size = float(t["size"])
            except (KeyError, TypeError, ValueError):
                continue
            sig = f"{tok}:{price}:{size}:{t.get('ts')}:{t.get('hash')}"
            if sig in seen_ws:
                continue
            seen_ws.add(sig)
            ws_levels[f"{tok}:{price:.4f}"] = now
            last_print[tok] = now
            rows.append({"asset": tok, "price": price, "size": size})
    return rows


def ws_leg_authoritative(w: dict[str, Any], token: str, now: float,
                         ws_connected: bool) -> bool:
    """True when the socket alone can be trusted for this leg's tape this tick.

    See WS_TOKEN_WARMUP / WS_AUTHORITY_HORIZON for why all three conditions are
    required. False is always the safe answer: it only costs one REST call.
    """
    if not ws_connected:
        return False
    ready_at = (w.get("ws_ready_at") or {}).get(token)
    if ready_at is None or (now - ready_at) < WS_TOKEN_WARMUP:
        return False
    last_print = (w.get("ws_last_print") or {}).get(token)
    if last_print is None or (now - last_print) > WS_AUTHORITY_HORIZON:
        return False
    return True


def prune_ws_levels(w: dict[str, Any], now: float) -> None:
    """Drop socket-printed levels older than the cross-source dedup TTL."""
    ws_levels: dict = w.get("ws_levels") or {}
    cutoff = now - WS_REST_DEDUP_TTL
    for key in [k for k, ts in ws_levels.items() if ts < cutoff]:
        ws_levels.pop(key, None)


@dataclass
class SeriesFetch:
    """What one worker thread brings back for one series. No shared state."""
    series: str
    duration: int
    label: str
    info: Optional[dict] = None
    err: str = ""
    up_book: dict = field(default_factory=dict)
    down_book: dict = field(default_factory=dict)


@dataclass
class PendingTick:
    """One series carried from the main-thread phases across the tape fan-out.

    A bare tuple made every field a positional puzzle at three separate
    unpacking sites; `SeriesFetch` above already set the pattern.
    """
    fetched: SeriesFetch
    cid: str
    window: dict
    tape: list[dict]
    missing: list[str]
    err: str


def _book_or_err(token_id: str, leg: str) -> dict:
    """Fetch one book, turning any failure into an empty book carrying `err`.

    Per-call isolation (Plan D2): one CLOB hiccup must not kill the collector.
    An earlier version let ReadTimeout propagate to main() and exit the process
    after four hours of work.
    """
    try:
        return full_book(CLOB_HOST, token_id)
    except Exception as e:
        return {"bids": {}, "asks": {}, "best_bid": None, "best_ask": None,
                "malformed": 0, "err": f"{leg}:{e}"}


def _ramp(stagger: float) -> None:
    """Hold a worker back so the slate ramps instead of firing as one burst.

    Jitter is applied to every worker including index 0: guarding the sleep on
    `stagger > 0` would leave exactly one series in the slate un-jittered on
    every tick, which is the one series most likely to collide with itself
    across consecutive rounds.
    """
    time.sleep(stagger + random.uniform(0.0, JITTER_SEC))


def fetch_series_books(series_slug: str, duration: int, label: str, now: float,
                       stagger: float = 0.0) -> SeriesFetch:
    """Resolve one series and fetch both of its books. Safe to run off-thread.

    Writes no files and touches neither `windows` nor `stats`. It does write
    `_gamma_cache` via `resolve_series_market`, and runs without a lock only
    because the slate gives each worker its own key: one job per SERIES entry,
    and `poll_once` blocks on the whole round before the next one starts, so no
    two workers ever write the same slug. `assert_unique_series_slugs` at import
    time is what keeps that invariant true rather than assumed.

    `stagger` spreads the slate's first requests instead of firing them as one
    burst (Plan D2/D4).
    """
    _ramp(stagger)
    try:
        info, err = resolve_series_market(series_slug, now)
    except Exception as e:
        return SeriesFetch(series_slug, duration, label, None, f"gamma err {e}")
    if not info:
        return SeriesFetch(series_slug, duration, label, None, err or "no live")
    return SeriesFetch(
        series_slug, duration, label, info, "",
        _book_or_err(info["up_token"], "up"),
        _book_or_err(info["down_token"], "down"),
    )


def fetch_slate_books(now: float) -> list[SeriesFetch]:
    """Fan the whole slate out; results come back in SERIES order."""
    jobs = list(enumerate(SERIES))
    return list(get_poll_executor().map(
        lambda job: fetch_series_books(job[1][0], job[1][1], job[1][2], now,
                                       job[0] * SERIES_STAGGER_SEC),
        jobs,
    ))


def fetch_slate_tapes(jobs: list[tuple[str, dict, list[str]]]
                      ) -> list[tuple[dict, str]]:
    """REST-tape only the legs the socket could not vouch for, concurrently.

    Each window owns its own `seen_tape` set, so no two workers share state.
    """
    def _one(job: tuple[int, str, dict, list[str]]) -> tuple[dict, str]:
        """Fetch one window's tape, or nothing when every leg is covered."""
        idx, cid, w, missing = job
        if not missing:
            return {}, ""
        _ramp(idx * SERIES_STAGGER_SEC)
        # `recent_trades` swallows its own HTTP and decode failures and returns
        # an empty dict, which is indistinguishable from a quiet market. The
        # sink makes a dead endpoint visible in snap["err"] instead of writing
        # an empty tape that looks like a legitimately silent second.
        soft: list[str] = []
        try:
            rows = recent_trades(cid, w["seen_tape"], limit=TAPE_LIMIT,
                                 on_error=soft.append)
        except Exception as e:
            return {}, f"tape:{e}"
        return rows, (f"tape:{soft[0]}" if soft else "")

    if not jobs:
        return []
    # Ramped like the book fan-out. This path bursts hardest exactly when it is
    # least welcome: a reconnect clears every leg's warm-up at once, so all ten
    # series need the tape endpoint in the same tick.
    return list(get_poll_executor().map(
        _one, [(i, cid, w, missing) for i, (cid, w, missing) in enumerate(jobs)]))


def poll_once(out_dir: Path, gzip: bool, stats: dict,
              ws_bridge: "Optional[CLOBStreamCollectorBridge]" = None,
              ) -> tuple[list[str], list[str]]:
    """One poll across all 10 series. Returns (closed_window_slugs, errors)."""
    now = time.time()
    day_key = now_day_key()
    iso_now = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
    tick_start = time.perf_counter()
    closed: list[str] = []
    errs: list[str] = []
    ws_connected = bool(ws_bridge is not None and getattr(ws_bridge, "is_connected", False))
    active_tokens: list[str] = []

    # A reconnect means the subscription was rebuilt and any print during the
    # gap is gone, so every token has to earn its warm-up again before its
    # silence can be read as "no trades".
    if ws_bridge is not None:
        try:
            reconnects = ws_bridge.get_status().get("reconnects", 0)
        except Exception:
            reconnects = stats.get("ws_reconnects", 0)
        if reconnects != stats.get("ws_reconnects", 0):
            for w in windows.values():
                w["ws_ready_at"] = {}
        stats["ws_reconnects"] = reconnects

    # Phase 1 — off-thread: resolve every series and fetch its two books. The
    # ten series are independent, so ~269ms each sequentially became one round
    # of roughly one series' latency (issue #167).
    fetches = fetch_slate_books(now)

    # Phase 2 — main thread, SERIES order: window upkeep and the socket tape.
    # Every mutation of `windows` and `stats` and every file append lives on
    # this thread, so the tick file stays ordered and needs no lock.
    pending: list[PendingTick] = []
    for fetched in fetches:
        series_slug, duration, label = fetched.series, fetched.duration, fetched.label
        info = fetched.info
        if not info:
            errs.append(f"{series_slug}:{fetched.err}")
            continue
        cid = info["conditionId"]
        if cid not in windows:
            windows[cid] = {
                "series": series_slug, "slug": info["slug"],
                "start_ts": info["start_ts"], "end_ts": info["end_ts"],
                "up_token": info["up_token"], "down_token": info["down_token"],
                "seen_tape": set(), "seen_ws": set(), "ws_levels": {},
                "ws_ready_at": {}, "ws_last_print": {},
                "snap_count": 0,
                "label": label, "duration": duration,
                "mids": [], "touch_pairs": [],
            }
        w = windows[cid]
        active_tokens.extend([w["up_token"], w["down_token"]])

        # Tape: the market socket sees every intra-second print, the 1-poll/s
        # REST tape sees ~1.4% of them. Socket first; REST only covers the
        # seconds it produced nothing (disconnected, or simply no prints yet).
        tape_list: list[dict] = []
        tape_err = ""
        prune_ws_levels(w, now)
        if ws_connected:
            try:
                tape_list = drain_ws_tape(ws_bridge, w, now)
                stats["tape_captured_ws"] = stats.get("tape_captured_ws", 0) + len(tape_list)
            except Exception as e:
                tape_err = f"ws_tape:{e}"
        # Fall back per token, not per snapshot: one leg printing on the socket
        # must not suppress the other leg's REST tape for that second. A leg the
        # socket is authoritative for skips the call entirely — on a healthy feed
        # most legs have no trades most seconds, so that REST round-trip was
        # latency spent to be told nothing happened.
        streamed = {t["asset"] for t in tape_list}
        missing = []
        for tok in (w["up_token"], w["down_token"]):
            if tok in streamed:
                continue
            if ws_leg_authoritative(w, tok, now, ws_connected):
                stats["tape_rest_skipped"] = stats.get("tape_rest_skipped", 0) + 1
                continue
            missing.append(tok)
        pending.append(PendingTick(fetched, cid, w, tape_list, missing, tape_err))

    # Phase 3 — off-thread: only the legs the socket could not vouch for pay a
    # REST round-trip, and those run concurrently too. On a healthy feed this
    # list is usually empty and the round costs nothing.
    tape_results = fetch_slate_tapes(
        [(p.cid, p.window, p.missing) for p in pending])

    # Phase 4 — main thread, SERIES order: merge, assemble, write.
    for pend, (tape_map, fetch_err) in zip(pending, tape_results):
        fetched, cid, w = pend.fetched, pend.cid, pend.window
        tape_list, missing = pend.tape, pend.missing
        series_slug, duration, label = fetched.series, fetched.duration, fetched.label
        info = fetched.info
        ub, db = fetched.up_book, fetched.down_book
        ub_err, db_err = ub.get("err"), db.get("err")
        # Both tape sources can fail in the same tick. `or` would report only
        # the socket's error and drop the REST one, which is worse than the
        # pre-#167 behaviour where the REST handler ran last and always won.
        tape_err = "; ".join(e for e in (pend.err, fetch_err) if e)
        if missing and tape_map:
            ws_levels = w.get("ws_levels") or {}
            for tok in missing:
                for p, s in tape_map.get(tok, {}).items():
                    if f"{tok}:{float(p):.4f}" in ws_levels:
                        continue  # already printed by the socket
                    tape_list.append({"asset": tok, "price": p, "size": s})
                    stats["tape_captured_rest"] = stats.get("tape_captured_rest", 0) + 1

        mid = compute_mid(ub)
        touch_pair = None
        if ub.get("best_ask") is not None and db.get("best_ask") is not None:
            touch_pair = ub["best_ask"] + db["best_ask"]
        if mid is not None:
            w["mids"].append(mid)
        if touch_pair is not None:
            w["touch_pairs"].append(touch_pair)
        resting_up = round(mid - SPREAD_OFFSET, 3) if mid is not None else None
        resting_pair = round(1.0 - 2 * SPREAD_OFFSET, 3)
        q_up = queue_ahead(ub.get("bids", {}), resting_up) if resting_up else None
        q_dn = queue_ahead(db.get("bids", {}),
                           round((1 - mid) - SPREAD_OFFSET, 3)) if mid is not None else None

        snap = {
            "ts": now, "iso": iso_now, "series": series_slug, "duration": duration,
            "label": label, "cid": cid, "slug": info["slug"],
            "start_ts": info["start_ts"], "end_ts": info["end_ts"],
            "t_rem": info["end_ts"] - now,
            "up_book": ub, "down_book": db,
            "tape_delta": tape_list,
            "mid": mid, "touch_pair": touch_pair,
            "resting_pair": resting_pair,
            "queue_up": q_up, "queue_down": q_dn,
            "err": ub_err or db_err or tape_err or None,
        }
        stats["lines"] = stats.get("lines", 0) + 1
        stats["series_seen"] = sorted(set(stats.get("series_seen", []) + [series_slug]))
        stats["day"] = day_key

        record_tape_sample(stats, now, bool(tape_list), len(tape_list))

        try:
            write_snap(snap, out_dir, day_key, gzip)
        except Exception as e:
            errs.append(f"write:{e}")
        w["snap_count"] += 1

    stats["ws_connected"] = ws_connected
    if ws_bridge is not None:
        try:
            ws_bridge.update_subscribed_tokens(sorted(set(active_tokens)))
            # Start the warm-up clock only once a token is both subscribed AND
            # the socket is actually up: a window opened this tick was not being
            # listened to during it, and a bridge that has not connected yet is
            # not listening at all. Stamping early would age the clock against
            # wall time the feed never spent delivering.
            if ws_connected:
                for w in windows.values():
                    ready = w.setdefault("ws_ready_at", {})
                    for tok in (w["up_token"], w["down_token"]):
                        ready.setdefault(tok, now)
        except Exception as e:
            errs.append(f"ws_sync:{e}")

    tick_ms = (time.perf_counter() - tick_start) * 1000.0
    stats["tick_ms_last"] = round(tick_ms, 1)
    # What a reader of run/ticks/*.jsonl actually gets between snapshots. It is
    # published because it is not POLL_INTERVAL and never was: replay, the
    # oscillation summary and queue telemetry all consume this series.
    #
    # Measured from successive `now` values rather than computed as round +
    # POLL_INTERVAL. Every snap in a round is stamped with `now`, so this is
    # the real gap between consecutive snapshot timestamps -- and the computed
    # form understated it, because main() also does restart, day-boundary,
    # alert and manifest work between rounds, and time.sleep only guarantees a
    # lower bound. The first round has no predecessor and keeps the estimate.
    prev_tick_ts = stats.get("_last_tick_ts")
    stats["sampling_interval_s"] = round(
        now - prev_tick_ts if prev_tick_ts is not None
        else tick_ms / 1000.0 + POLL_INTERVAL, 2)
    stats["_last_tick_ts"] = now
    if "tick_ms_first" not in stats:
        # Charging the opening round against TICK_BUDGET_MS would fire slow_tick
        # on every `--once` run — the exact false positive issue #167 set out to
        # remove — so it is judged against the looser COLD_TICK_BUDGET_MS.
        stats["tick_ms_first"] = round(tick_ms, 1)
        if tick_ms > COLD_TICK_BUDGET_MS:
            errs.append(f"slow_first_tick:{tick_ms:.0f}ms")
    else:
        stats["tick_ms_max"] = round(max(tick_ms, stats.get("tick_ms_max", 0.0)), 1)
        if tick_ms > TICK_BUDGET_MS:
            errs.append(f"slow_tick:{tick_ms:.0f}ms")

    closed_now = 0
    run_dir = run_dir_for(out_dir)
    for cid, w in list(windows.items()):
        if w["end_ts"] < now:
            closed.append(w["slug"])
            try:
                rec = finalize_window(
                    [m for m in w.get("mids", []) if m is not None],
                    [t for t in w.get("touch_pairs", []) if t is not None],
                    {
                        "series": w.get("series", ""),
                        "label": w.get("label", ""),
                        "duration": w.get("duration", 300),
                        "cid": cid,
                        "slug": w.get("slug", ""),
                        "start_ts": w.get("start_ts", 0.0),
                        "end_ts": w.get("end_ts", 0.0),
                        "closed_ts": now,
                        "snaps": w.get("snap_count", 0),
                    },
                )
                if rec.get("class") != "no_data":
                    write_window(rec, run_dir)
                    closed_now += 1
            except Exception as e:
                errs.append(f"window:{cid}:{e}")
            # Empty the socket buffer before the tokens drop out of the
            # subscription next tick: nothing will ever drain them again, and a
            # straggler print would otherwise re-create the entry for good.
            if ws_bridge is not None:
                try:
                    for tok in (w.get("up_token"), w.get("down_token")):
                        if tok:
                            ws_bridge.drain_trades_for_token(tok)
                except Exception as e:
                    errs.append(f"ws_drain:{cid}:{e}")
            del windows[cid]
    if closed_now:
        try:
            refresh_summary(run_dir)
        except Exception as e:
            errs.append(f"summary:{e}")
    return closed, errs


def start_ws_bridge(disabled: bool = False) -> "Optional[CLOBStreamCollectorBridge]":
    """Start the CLOB market stream bridge, or None when disabled/unavailable.

    A socket that cannot be opened is never fatal: the collector keeps running
    on the REST tape exactly as it did before issue #165.
    """
    if disabled:
        return None
    try:
        from strategy.streaming import CLOBStreamCollectorBridge
        bridge = CLOBStreamCollectorBridge()
        bridge.start()
        return bridge
    except Exception as e:
        print(f"ws bridge unavailable ({e}); falling back to REST tape")
        return None


MAX_WS_RESTARTS = 5


def restart_ws_bridge_if_dead(
    ws_bridge: "Optional[CLOBStreamCollectorBridge]",
    stats: dict,
    disabled: bool = False,
) -> "Optional[CLOBStreamCollectorBridge]":
    """Rebuild the bridge if its worker thread died, up to `MAX_WS_RESTARTS`.

    Socket drops are retried inside the client; a dead *thread* is a bug, and
    without this the collector would serve REST-only for the rest of a run
    that is meant to last days. The cap stops a reproducible crash from
    thrashing restarts every second.
    """
    if disabled or ws_bridge is None or ws_bridge.is_running:
        return ws_bridge
    restarts = stats.get("ws_restarts", 0)
    if restarts >= MAX_WS_RESTARTS:
        return ws_bridge
    stats["ws_restarts"] = restarts + 1
    print(f"ws bridge thread died; restarting ({restarts + 1}/{MAX_WS_RESTARTS})")
    stop_ws_bridge(ws_bridge)
    fresh = start_ws_bridge(disabled=False)
    stats["ws_enabled"] = fresh is not None
    return fresh


def stop_ws_bridge(ws_bridge: "Optional[CLOBStreamCollectorBridge]") -> None:
    """Stop the stream bridge if one is running."""
    if ws_bridge is None:
        return
    try:
        ws_bridge.stop()
    except Exception as e:
        print(f"ws bridge shutdown error: {e}")


def install_signal_handlers() -> None:
    """Route SIGINT/SIGTERM into the existing KeyboardInterrupt shutdown path."""
    import signal

    def _raise_interrupt(signum: int, frame: Any) -> None:
        """Turn a termination signal into the loop's normal exit."""
        raise KeyboardInterrupt

    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _raise_interrupt)
        except (ValueError, OSError, RuntimeError):
            continue  # not the main thread, or unsupported on this platform


def main():
    """Run full-depth 1-second tick collection across 10 Polymarket series."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single poll then exit")
    ap.add_argument("--days", type=int, default=0, help="run until N UTC day boundaries pass")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory")
    ap.add_argument("--gzip", action="store_true", help="rotate daily file as .jsonl.gz")
    ap.add_argument("--no-ws", action="store_true",
                    help="disable the CLOB market WebSocket tape stream (REST polling only)")
    args = ap.parse_args()

    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    stats: dict = {
        "lines": 0,
        "series_seen": [],
        "tape_empty_count": 0,
        "tape_non_empty_count": 0,
        "tape_empty_rate": 0.0,
        "tape_recent_empty_rate": 0.0,
        "tape_alert": False,
        "tape_entries_total": 0,
        "ws_enabled": False,
        "ws_connected": False,
        "ws_reconnects": 0,
        "tape_captured_ws": 0,
        "tape_captured_rest": 0,
        "tape_rest_skipped": 0,
        "ws_restarts": 0,
        "tick_ms_last": 0.0,
        "tick_ms_max": 0.0,
        "sampling_interval_s": 0.0,
    }

    ws_bridge = start_ws_bridge(disabled=args.no_ws)
    stats["ws_enabled"] = ws_bridge is not None
    install_signal_handlers()

    print(f"collect_ticks: {len(SERIES)} series -> {out_dir}  gzip={args.gzip}  "
          f"ws={'on' if ws_bridge is not None else 'off'}")
    if args.once:
        try:
            closed, errs = poll_once(out_dir, args.gzip, stats, ws_bridge=ws_bridge)
            update_manifest(out_dir, stats)
        finally:
            stop_ws_bridge(ws_bridge)
            shutdown_poll_executor()
        print(
            f"once done · closed={len(closed)} errs={len(errs)} · "
            f"round={stats.get('tick_ms_last', 0.0):.0f}ms "
            f"(cold start; warm rounds are several times faster) · "
            f"tape_empty_rate={stats.get('tape_empty_rate', 0.0):.1%} · "
            f"ws_trades={stats.get('tape_captured_ws', 0)}"
        )
        return

    day_boundaries = 0
    current_day = now_day_key()
    try:
        while True:
            closed, errs = poll_once(out_dir, args.gzip, stats, ws_bridge=ws_bridge)
            ws_bridge = restart_ws_bridge_if_dead(ws_bridge, stats, disabled=args.no_ws)
            new_day = now_day_key()
            if new_day != current_day:
                day_boundaries += 1
                current_day = new_day
                update_manifest(out_dir, stats)
                if args.days and day_boundaries >= args.days:
                    print(f"reached {args.days} day boundaries, exiting")
                    break
            if closed:
                print(f"closed {len(closed)} window(s)  errs={len(errs)}")

            # Check for sustained tape silence alert (>99% empty over rolling 5m window)
            if stats.get("tape_alert"):
                now_ts = time.time()
                last_alert = stats.get("_last_alert_log_ts", 0.0)
                if now_ts - last_alert >= 60.0:
                    stats["_last_alert_log_ts"] = now_ts
                    print(
                        f"⚠️ WARNING: Sustained 5m tape silence (recent_empty_rate = "
                        f"{stats.get('tape_recent_empty_rate', 0.0):.1%}) — check CLOB trade stream"
                    )

            if int(time.time()) % 10 == 0:
                update_manifest(out_dir, stats)
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("interrupted")
    finally:
        stop_ws_bridge(ws_bridge)
        shutdown_poll_executor()
        update_manifest(out_dir, stats)


if __name__ == "__main__":
    main()
