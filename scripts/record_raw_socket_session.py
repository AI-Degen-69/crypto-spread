"""Record raw CLOB WebSocket messages alongside REST book ground-truth for reconciliation.

Diagnostic session recorder for Issue #359:
Captures raw WebSocket market events (as dispatched by the venue) concurrently
with 1-second REST book snapshots (`full_book()`) for active series tokens.
The recorded JSONL stream feeds `scripts/replay_socket_reconciliation.py`.

A session with no REST snapshots is not a usable input: every reconciliation
comparison in the replay depends on that ground truth (issue #438). Both REST
failure modes used to be silent — an empty book was skipped with no log, and an
exception was downgraded to a warning — so a ground-truth-less capture looked
like a healthy one. The three outcomes are now counted apart per token and a
zero-REST session exits non-zero.

Usage:
    python -m scripts.record_raw_socket_session --seconds 30
    python -m scripts.record_raw_socket_session --series sol-up-or-down-5m --seconds 15
"""
from __future__ import annotations

import argparse
import collections
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.collect_ticks import fetch_live_for_series  # noqa: E402
from strategy.markets import CLOB_HOST, full_book  # noqa: E402
from strategy.series import SERIES  # noqa: E402
from strategy.streaming import CLOBMarketWSClient, CLOBStreamCollectorBridge  # noqa: E402

log = logging.getLogger("record_raw_ws")


class SessionResult(NamedTuple):
    """Outcome of one capture session, so the caller can fail on a bad capture."""

    out_file: Path
    ws_events: int
    rest_snapshots: int
    per_token_ws: Dict[str, int]
    per_token_rest: Dict[str, int]


def _event_tokens(ev: Dict[str, Any]) -> set:
    """Token ids named by one raw WS event.

    Mirrors the extraction the reconciliation replay performs
    (`SocketReconciler._handle_ws_event`): a frame may name its token via
    `asset_id`/`token_id`, or per entry under `price_changes`/`changes`.
    An event that names no usable token contributes nothing and never raises.
    """
    tokens: set = set()
    asset_id = ev.get("asset_id") or ev.get("token_id")
    if asset_id:
        tokens.add(str(asset_id))
    changes = ev.get("price_changes") or ev.get("changes") or []
    if isinstance(changes, list):
        for change in changes:
            if isinstance(change, dict):
                token = change.get("asset_id") or change.get("token_id")
                if token:
                    tokens.add(str(token))
    return tokens


def capture_verdict(ws_events: int, rest_snapshots: int) -> Tuple[int, Optional[str]]:
    """Exit code and failure reason for a finished session.

    REST ground truth is what makes a session replayable; without it every
    comparison in the reconciliation replay collapses to zero comparisons, and
    the 16.8% residual this recorder exists to explain stays unmeasurable
    (issue #438). A capture that recorded nothing, or recorded WS traffic with
    no REST counterpart, is therefore a failure and must not exit zero.
    """
    if rest_snapshots == 0 and ws_events == 0:
        return 1, "no WS events and no REST snapshots — the capture recorded nothing"
    if rest_snapshots == 0:
        return 1, ("no REST snapshots captured — a session without ground truth "
                   "is not a usable input")
    return 0, None


def new_rest_stats() -> Dict[str, collections.Counter]:
    """Per-token REST outcome counters, kept apart on purpose.

    `rest_ok` is a recorded snapshot, `rest_empty` a book the venue served as
    empty or unparseable, and `rest_error` a raised fetch. Collapsing the last
    two into one number is what let a fully-failed REST side look healthy.
    """
    return {
        "rest_ok": collections.Counter(),
        "rest_empty": collections.Counter(),
        "rest_error": collections.Counter(),
    }


def poll_rest_once(markets: List[Tuple[str, str, str]],
                   recorder: RawStreamRecorder,
                   stats: Dict[str, collections.Counter],
                   fetch: Callable[..., Optional[Dict[str, Any]]] = full_book) -> None:
    """One REST sweep over every subscribed token, counting each outcome.

    `fetch` is injectable so the three outcomes can be tested without the
    network. An empty book and a raised exception are logged distinctly, and
    both are counted: neither is allowed to be silent (issue #438).
    """
    for slug, up_tok, dn_tok in markets:
        for token in (up_tok, dn_tok):
            try:
                book = fetch(CLOB_HOST, token)
            except Exception as exc:
                stats["rest_error"][token] += 1
                log.warning("REST full_book raised for %s: %s: %s",
                            token, type(exc).__name__, exc)
                continue
            if not book:
                stats["rest_empty"][token] += 1
                log.warning("REST full_book returned no book for %s", token)
                continue
            recorder.write_rest_snapshot(slug, token, book)
            stats["rest_ok"][token] += 1


class RawStreamRecorder:
    """Thread-safe append-only sink for raw WS events and concurrent REST books."""

    def __init__(self, out_path: Path):
        """Initialize the recorder and open destination JSONL log."""
        self.out_path = out_path
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._fh = open(self.out_path, "a", encoding="utf-8", newline="\n")
        self.ws_event_count = 0
        self.rest_snapshot_count = 0
        self.ws_by_token: collections.Counter = collections.Counter()

    def write_ws_event(self, ev: Dict[str, Any]) -> None:
        """Record one raw WS event as dispatched."""
        record = {
            "type": "ws",
            "rx": time.time(),
            "mono": time.perf_counter(),
            "ev": ev,
        }
        line = json.dumps(record, separators=(",", ":"))
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()
            self.ws_event_count += 1
            for token in _event_tokens(ev):
                self.ws_by_token[token] += 1

    def write_rest_snapshot(self, series: str, token: str, book: Dict[str, Any]) -> None:
        """Record one REST ground truth book snapshot."""
        record = {
            "type": "rest",
            "rx": time.time(),
            "mono": time.perf_counter(),
            "series": series,
            "token": token,
            "book": book,
        }
        line = json.dumps(record, separators=(",", ":"))
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()
            self.rest_snapshot_count += 1

    def close(self) -> None:
        """Flush and close the underlying file handle."""
        with self._lock:
            if not self._fh.closed:
                self._fh.flush()
                self._fh.close()


class DiagnosticRawClient(CLOBMarketWSClient):
    """CLOBMarketWSClient capturing every event into the recorder sink before processing."""

    def __init__(self, *args, recorder: Optional[RawStreamRecorder] = None, **kwargs):
        """Initialize diagnostic client wrapping market client with an optional event recorder."""
        super().__init__(*args, **kwargs)
        self._recorder = recorder

    def _handle_event(self, ev: Dict[str, Any]) -> None:
        """Intercept raw event and persist before delegating to base handler."""
        if self._recorder is not None:
            try:
                self._recorder.write_ws_event(ev)
            except Exception as e:
                log.debug("error writing raw WS event: %s", e)
        super()._handle_event(ev)


def resolve_live_markets(series_filter: Optional[List[str]] = None) -> List[Tuple[str, str, str]]:
    """Resolve live markets: returns list of (series_slug, up_token, down_token)."""
    results: List[Tuple[str, str, str]] = []
    series_list = SERIES
    if series_filter:
        series_set = set(series_filter)
        series_list = [s for s in SERIES if s[0] in series_set]

    for slug, _dur, _label in series_list:
        info, err = fetch_live_for_series(slug)
        if info and not err:
            up_tok = str(info.get("up_token") or "")
            dn_tok = str(info.get("down_token") or "")
            if up_tok and dn_tok:
                results.append((slug, up_tok, dn_tok))
    return results


def run_session(
    seconds: float = 30.0,
    series_filter: Optional[List[str]] = None,
    out_dir: Optional[Path] = None,
    poll_rest_interval: float = 1.0,
) -> SessionResult:
    """Execute a bounded raw socket + REST capture session.

    Returns the session's counts so `main` can fail loudly on a capture that
    carries no REST ground truth (issue #438).
    """
    if out_dir is None:
        out_dir = ROOT / "run" / "diag_ws"
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    out_file = out_dir / f"raw_session_{stamp}.jsonl"
    recorder = RawStreamRecorder(out_file)

    markets = resolve_live_markets(series_filter)
    if not markets:
        print("No live markets resolved to capture.")
        recorder.close()
        return SessionResult(out_file, 0, 0, {}, {})

    all_tokens: List[str] = []
    token_to_series: Dict[str, str] = {}
    for slug, up_tok, dn_tok in markets:
        all_tokens.extend([up_tok, dn_tok])
        token_to_series[up_tok] = slug
        token_to_series[dn_tok] = slug

    print(f"Subscribing to {len(all_tokens)} tokens across {len(markets)} series...")
    bridge = CLOBStreamCollectorBridge(token_ids=all_tokens)
    # Swap in the diagnostic client before starting the thread
    bridge.client = DiagnosticRawClient(token_ids=all_tokens, recorder=recorder)
    bridge.start()

    t_start = time.time()
    last_rest_poll = 0.0
    stats = new_rest_stats()
    print(f"Capturing diagnostic session to {out_file} (duration: {seconds:.1f}s)...")

    try:
        while True:
            elapsed = time.time() - t_start
            if seconds > 0 and elapsed >= seconds:
                break

            now = time.time()
            if now - last_rest_poll >= poll_rest_interval:
                last_rest_poll = now
                # One REST sweep, every outcome counted per token (issue #438).
                poll_rest_once(markets, recorder, stats)

            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\nSession interrupted by user.")
    finally:
        bridge.stop()
        recorder.close()

    print(
        f"Session complete: {recorder.ws_event_count} WS events, "
        f"{recorder.rest_snapshot_count} REST snapshots saved to {out_file}"
    )
    tokens = sorted(set(recorder.ws_by_token) | set(stats["rest_ok"])
                    | set(stats["rest_empty"]) | set(stats["rest_error"]))
    print("Per token (ws events / rest snapshots / empty books / fetch errors):")
    for token in tokens:
        print(f"  {token}  ws={recorder.ws_by_token.get(token, 0)} "
              f"rest={stats['rest_ok'].get(token, 0)} "
              f"empty={stats['rest_empty'].get(token, 0)} "
              f"errors={stats['rest_error'].get(token, 0)}")
    return SessionResult(
        out_file=out_file,
        ws_events=recorder.ws_event_count,
        rest_snapshots=recorder.rest_snapshot_count,
        per_token_ws=dict(recorder.ws_by_token),
        per_token_rest={token: stats["rest_ok"].get(token, 0) for token in tokens},
    )


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point for recording raw socket and REST book sessions."""
    parser = argparse.ArgumentParser(description="Capture raw WS events and concurrent REST books")
    parser.add_argument("--seconds", type=float, default=30.0, help="Duration in seconds (0 = infinite)")
    parser.add_argument("--series", nargs="*", default=None, help="Series slugs to track")
    parser.add_argument("--out-dir", type=str, default=None, help="Output directory")
    parser.add_argument("--poll-rest-interval", type=float, default=1.0, help="REST book polling interval")
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir) if args.out_dir else None
    result = run_session(
        seconds=args.seconds,
        series_filter=args.series,
        out_dir=out_dir,
        poll_rest_interval=args.poll_rest_interval,
    )
    code, reason = capture_verdict(result.ws_events, result.rest_snapshots)
    if reason is not None:
        print(f"FAILURE: {reason}")
    return code


if __name__ == "__main__":
    sys.exit(main())
