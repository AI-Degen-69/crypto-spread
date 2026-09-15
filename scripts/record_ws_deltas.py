"""Record raw CLOB WebSocket book events at socket resolution.

The tick collector samples the book on a ~1.44s loop. Measured against that
capture, 336 two-sided arbitrage opportunities existed and **99.1% were gone by
the next snapshot** -- so the dataset can only establish that they live for
less than one sampling interval, never how much less. That is a property of the
instrument, not of the market: 1.44s is the resolution floor.

The socket already delivers every `price_change` individually. The collector
folds them into its book and throws the timing away. This records each event as
it arrives, stamped on receipt, so the question the snapshots cannot answer --
how long does an opportunity actually last, and what latency budget would be
needed to take it -- becomes measurable.

Deliberately a separate process:

  * `scripts/collect_ticks.py` must not be restarted mid-capture, and this
    needs none of its state;
  * it opens its own socket and subscribes to the same tokens, so a failure
    here cannot take the capture down with it;
  * its output is append-only JSONL beside the tick files, on the same daily
    rotation, so the two are joinable by timestamp.

    python -m scripts.record_ws_deltas
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.collect_ticks import fetch_live_for_series  # noqa: E402
from strategy.series import SERIES  # noqa: E402
from strategy.streaming import CLOBMarketWSClient, CLOBStreamCollectorBridge  # noqa: E402

OUT_DIR = ROOT / "run" / "ws_deltas"
STATUS = OUT_DIR / "status.json"

#: How often to re-resolve which markets are live. Windows roll every 5 or 15
#: minutes, and a subscription to an expired token records nothing.
RESOLVE_INTERVAL = 30.0

#: Events worth the disk. `book` is the periodic full snapshot the venue sends,
#: `price_change` is the per-level delta, `best_bid_ask` is the top-of-book
#: summary -- between them they reconstruct the top of book at socket
#: resolution. `last_trade_price` is already captured by the tick collector.
RECORDED = ("book", "price_change", "best_bid_ask", "tick_size_change")


class RecordingClient(CLOBMarketWSClient):
    """A market client that writes every event it dispatches.

    Subclassed rather than patched: `streaming.py` is imported by the running
    collector, and editing it would change that process's behaviour on its next
    restart. Overriding here keeps the blast radius inside this file.
    """

    def __init__(self, *a, recorder=None, **kw):
        """Wrap the market client with a recorder sink."""
        super().__init__(*a, **kw)
        self._recorder = recorder

    def _handle_event(self, ev: Dict[str, Any]) -> None:
        """Record the event, then let the normal book-keeping run.

        Recording happens first and unconditionally, ahead of any filtering the
        parent applies. This recorder is the instrument, so what it captures
        must not depend on whether the client under it currently understands a
        frame -- which is not hypothetical: writing this is what surfaced #188,
        where every `price_change` frame was being discarded because the token
        was looked for at the top level and the venue puts one per entry.
        Recording downstream of that filter would have captured nothing and
        shown nothing to be wrong.
        """
        if self._recorder is not None:
            try:
                self._recorder.write(ev)
            except Exception:
                # Recording must never break the socket's own bookkeeping.
                pass
        super()._handle_event(ev)


class Recorder:
    """Append-only JSONL sink with daily rotation and counters.

    Every line carries `rx` -- the wall clock at the moment the frame was
    dispatched -- and `mono`, a monotonic counter. `rx` is what joins these to
    the tick files; `mono` is what survives a clock adjustment, which matters
    because the whole point is measuring sub-second durations.
    """

    def __init__(self, out_dir: Path):
        """Open the sink; the file itself is opened lazily per day."""
        self.dir = out_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._day = ""
        self._fh = None
        self._lock = threading.Lock()
        self.counts: Dict[str, int] = {}
        #: token -> last written (best_bid, best_ask) as FLOATS, so a repeated
        #: quote is not written again. This is the difference between 14 GB/day
        #: and something that fits.
        #:
        #: Floats because both branches share this map and the venue is not
        #: consistent about its own encoding: a `price_change` reports an ask
        #: of `"1"` where a `book` snapshot yields `1.0`. Keyed on the raw
        #: strings the two never matched, so every `book` that merely repeated
        #: the preceding quote was written again, and the file carried two
        #: spellings of the same number for consumers to normalize.
        self._last: Dict[str, tuple] = {}
        self.total = 0
        self.started = time.time()

    def _file_for(self, day: str):
        """The open handle for `day`, rotating the file when the UTC day turns."""
        if day != self._day:
            if self._fh is not None:
                self._fh.close()
            self._fh = open(self.dir / f"deltas_{day}.jsonl", "a",
                            encoding="utf-8", newline="\n")
            self._day = day
        return self._fh

    def write(self, ev: Dict[str, Any]) -> None:
        """Append top-of-book changes, one line per token that actually moved.

        Every `price_changes` entry carries `asset_id`, `best_bid` and
        `best_ask`, so the top of book arrives with each delta and no book
        reconstruction is needed.

        Writing only when a token's top of book actually moves is what makes
        this affordable. The raw stream measured ~1,000 events/s and would cost
        14 GB/day, and the large majority repeat a quote already known -- which
        answers nothing about how long an opportunity lasts.
        """
        et = str(ev.get("event_type") or ev.get("type") or "").lower()
        now = time.time()
        mono = time.perf_counter()
        venue = ev.get("timestamp")
        out: List[str] = []

        if et == "price_change":
            for c in (ev.get("price_changes") or ev.get("changes") or []):
                if not isinstance(c, dict):
                    continue
                tok = str(c.get("asset_id") or "").strip()
                if not tok:
                    continue
                try:
                    bb, ba = float(c.get("best_bid")), float(c.get("best_ask"))
                except (TypeError, ValueError):
                    continue
                if self._last.get(tok) == (bb, ba):
                    continue        # top of book unchanged; nothing to learn
                self._last[tok] = (bb, ba)
                out.append(json.dumps(
                    {"rx": round(now, 6), "mono": round(mono, 6), "et": "top",
                     "tok": tok, "bb": bb, "ba": ba, "vts": venue},
                    separators=(",", ":")))
        elif et == "book":
            tok = str(ev.get("asset_id") or ev.get("token_id") or "").strip()
            if not tok:
                return

            def best(side: str, pick):
                """Best price on one side of a full book snapshot."""
                rows = [float(x.get("price")) for x in (ev.get(side) or [])
                        if isinstance(x, dict) and x.get("price") is not None]
                return pick(rows) if rows else None

            bb_f, ba_f = best("bids", max), best("asks", min)
            if bb_f is None or ba_f is None:
                return
            if self._last.get(tok) == (bb_f, ba_f):
                return
            self._last[tok] = (bb_f, ba_f)
            out.append(json.dumps(
                {"rx": round(now, 6), "mono": round(mono, 6), "et": "snap",
                 "tok": tok, "bb": bb_f, "ba": ba_f, "vts": venue},
                separators=(",", ":")))
        else:
            return

        if not out:
            return
        day = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            f = self._file_for(day)
            for line in out:
                f.write(line + "\n")
            f.flush()   # a crash must not cost the buffer; volume is modest
            self.counts[et] = self.counts.get(et, 0) + len(out)
            self.total += len(out)

    def snapshot(self) -> dict:
        """Counters for the status file."""
        with self._lock:
            el = max(1e-6, time.time() - self.started)
            return {"total": self.total, "by_type": dict(self.counts),
                    "events_per_sec": round(self.total / el, 2),
                    "uptime_sec": round(el, 1), "ts": time.time()}


def live_tokens() -> List[str]:
    """Every token id of the currently live market in each series."""
    toks: List[str] = []
    for slug, _dur, _label in SERIES:
        info, _err = fetch_live_for_series(slug)
        if info:
            toks.append(str(info["up_token"]))
            toks.append(str(info["down_token"]))
    return sorted(set(toks))


def main(argv: List[str]) -> int:
    """Subscribe to every live token and record until interrupted."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=0.0,
                    help="stop after this long (0 = run until killed)")
    ap.add_argument("--status-interval", type=float, default=30.0)
    a = ap.parse_args(argv)

    rec = Recorder(OUT_DIR)
    toks = live_tokens()
    if not toks:
        print("no live tokens resolved; nothing to subscribe to")
        return 1
    print(f"subscribing to {len(toks)} tokens across {len(SERIES)} series")

    bridge = CLOBStreamCollectorBridge(token_ids=toks)
    # Swap in the recording client before the socket thread starts.
    bridge.client = RecordingClient(token_ids=toks, recorder=rec)
    bridge.start()

    t0 = time.time()
    last_resolve = t0
    last_status = 0.0
    try:
        while True:
            time.sleep(1.0)
            now = time.time()
            if a.seconds and now - t0 >= a.seconds:
                break
            if now - last_resolve >= RESOLVE_INTERVAL:
                last_resolve = now
                try:
                    fresh = live_tokens()
                    if fresh and set(fresh) != set(bridge.client.token_ids):
                        bridge.update_subscribed_tokens(fresh)
                        print(f"resubscribed: {len(fresh)} tokens")
                except Exception as e:
                    print(f"resolve failed: {e}")
            if now - last_status >= a.status_interval:
                last_status = now
                st = rec.snapshot()
                st["connected"] = bool(bridge.is_connected)
                st["tokens"] = len(bridge.client.token_ids)
                STATUS.write_text(json.dumps(st, indent=1), encoding="utf-8")
                print(f"  {st['total']} events  {st['events_per_sec']}/s  "
                      f"connected={st['connected']}  {st['by_type']}")
    except KeyboardInterrupt:
        print("interrupted")
    finally:
        try:
            bridge.stop()
        except Exception:
            pass
        st = rec.snapshot()
        STATUS.write_text(json.dumps(st, indent=1), encoding="utf-8")
        print(f"recorded {st['total']} events in {st['uptime_sec']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
