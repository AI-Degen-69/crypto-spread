"""Contract tests for the CLOB market WebSocket tape collector (issue #165).

Covers the three layers the 100%-tape-capture path is built from:

1. `CLOBMarketWSClient` direct-WebSocket protocol handling — `book`,
   `price_change`, `last_trade_price`, `best_bid_ask`, `PONG`, the 10s `PING`
   keepalive and the exponential-backoff reconnect schedule.
2. `CLOBStreamCollectorBridge` — the daemon-thread asyncio runner and its
   thread-safe drain/book/status surface.
3. `scripts.collect_ticks.poll_once` — draining streamed prints into
   `tape_delta`, falling back to REST when the socket is down, and the
   TTL dedup that stops a print being delivered twice across the two sources.

No test in this file touches the network: every socket is a fake driven by a
scripted list of frames.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest

from strategy.streaming import (
    CLOB_WS_URL,
    CLOBMarketWSClient,
    CLOBStreamCollectorBridge,
)


# --------------------------------------------------------------------------
# Fake WebSocket plumbing
# --------------------------------------------------------------------------

class FakeWS:
    """Scripted websocket: replays `frames`, then blocks until stopped."""

    def __init__(self, frames: list[str], fail_after: bool = False):
        self.frames = list(frames)
        self.sent: list[str] = []
        self.fail_after = fail_after
        self.closed = False

    async def send(self, msg: str) -> None:
        self.sent.append(msg)

    async def recv(self) -> str:
        if self.frames:
            return self.frames.pop(0)
        if self.fail_after:
            raise ConnectionError("socket closed by peer")
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    async def close(self) -> None:
        self.closed = True

    async def __aenter__(self) -> "FakeWS":
        return self

    async def __aexit__(self, *exc) -> bool:
        self.closed = True
        return False


class FakeConnector:
    """Callable standing in for `websockets.connect`; hands out FakeWS sockets."""

    def __init__(self, sockets: list[FakeWS]):
        self.sockets = list(sockets)
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, **kwargs) -> FakeWS:
        self.calls.append((url, kwargs))
        if self.sockets:
            return self.sockets.pop(0)
        return FakeWS([], fail_after=False)


def _trade_frame(asset: str, price: str, size: str, side: str = "BUY",
                 ts: str = "1700000000000", tx: str = "0xfeed") -> str:
    return json.dumps([{
        "event_type": "last_trade_price",
        "asset_id": asset,
        "market": "0xCID",
        "price": price,
        "size": size,
        "side": side,
        "timestamp": ts,
        "transaction_hash": tx,
    }])


# --------------------------------------------------------------------------
# 1. Protocol parsing
# --------------------------------------------------------------------------

def test_clob_market_handles_book_snapshot_frame():
    """A raw `book` frame replaces local book state wholesale."""
    client = CLOBMarketWSClient(token_ids=["tok_up"])
    client.handle_raw_message(json.dumps({
        "event_type": "book",
        "asset_id": "tok_up",
        "bids": [{"price": "0.48", "size": "100"}, {"price": "0.47", "size": "50"}],
        "asks": [{"price": "0.53", "size": "80"}],
    }))
    book = client.books["tok_up"]
    assert book["best_bid"] == 0.48
    assert book["best_ask"] == 0.53
    assert book["bids"][0.47] == 50.0


def test_clob_market_handles_price_change_frame():
    """`price_change` frames mutate single levels, size 0 deletes the level."""
    client = CLOBMarketWSClient()
    client.apply_book_snapshot("tok_up", [{"price": "0.48", "size": "100"}],
                               [{"price": "0.53", "size": "80"}])
    client.handle_raw_message(json.dumps({
        "event_type": "price_change",
        "asset_id": "tok_up",
        "changes": [{"price": "0.50", "side": "BUY", "size": "25"}],
    }))
    assert client.books["tok_up"]["best_bid"] == 0.50
    client.handle_raw_message(json.dumps({
        "event_type": "price_change",
        "asset_id": "tok_up",
        "changes": [{"price": "0.50", "side": "BUY", "size": "0"}],
    }))
    assert client.books["tok_up"]["best_bid"] == 0.48


def test_clob_market_parses_last_trade_price_into_buffer():
    """`last_trade_price` is parsed into a typed trade record and buffered."""
    seen: list[dict] = []
    client = CLOBMarketWSClient(on_trade=seen.append)
    client.handle_raw_message(_trade_frame("tok_up", "0.47", "120", side="SELL"))

    drained = client.drain_trades("tok_up")
    assert len(drained) == 1
    t = drained[0]
    assert t["asset"] == "tok_up"
    assert t["price"] == 0.47
    assert t["size"] == 120.0
    assert t["side"] == "SELL"
    assert t["ts"] == 1700000000000
    assert t["hash"] == "0xfeed"
    assert isinstance(t["price"], float) and isinstance(t["size"], float)
    assert seen and seen[0]["asset"] == "tok_up"
    assert client.trades_captured == 1
    # drain is destructive
    assert client.drain_trades("tok_up") == []


def test_clob_market_drain_without_token_returns_every_asset():
    """`drain_trades()` with no token flattens the whole buffer."""
    client = CLOBMarketWSClient()
    client.handle_raw_message(_trade_frame("tok_up", "0.47", "10"))
    client.handle_raw_message(_trade_frame("tok_dn", "0.51", "20"))
    drained = client.drain_trades()
    assert sorted(t["asset"] for t in drained) == ["tok_dn", "tok_up"]
    assert client.drain_trades() == []


def test_clob_market_handles_best_bid_ask_and_pong():
    """`best_bid_ask` records top of book; a bare PONG stamps the heartbeat."""
    client = CLOBMarketWSClient()
    client.handle_raw_message(json.dumps({
        "event_type": "best_bid_ask",
        "asset_id": "tok_up",
        "best_bid": "0.49",
        "best_ask": "0.52",
    }))
    assert client.top_of_book["tok_up"] == {"best_bid": 0.49, "best_ask": 0.52}

    assert client.last_pong_ts == 0.0
    client.handle_raw_message("PONG")
    assert client.last_pong_ts > 0.0


def test_clob_market_ignores_malformed_frames():
    """Garbage frames are dropped, never raised — the collector must not die."""
    client = CLOBMarketWSClient()
    for bad in ["not json", "[]", "{}", json.dumps({"event_type": "book"}),
                json.dumps({"event_type": "last_trade_price", "asset_id": "t",
                            "price": "abc", "size": "1"})]:
        client.handle_raw_message(bad)
    assert client.drain_trades() == []
    assert client.books == {}


# --------------------------------------------------------------------------
# 2. Keepalive, backoff, subscriptions
# --------------------------------------------------------------------------

def test_backoff_schedule_is_exponential_and_capped():
    """Backoff doubles from 1s to a 30s ceiling, jitter stays inside the step."""
    client = CLOBMarketWSClient()
    delays = []
    cur = client.backoff_base
    for _ in range(8):
        delays.append(cur)
        cur = client.next_backoff(cur)
    assert delays[:5] == [1.0, 2.0, 4.0, 8.0, 16.0]
    assert delays[-1] == 30.0
    assert client.next_backoff(30.0) == 30.0

    for base in (1.0, 4.0, 30.0):
        for _ in range(20):
            j = client.jittered(base)
            assert base <= j <= base * 1.25


def test_update_tokens_bumps_version_and_builds_subscription():
    """Token rotation bumps the version so the socket resubscribes."""
    client = CLOBMarketWSClient(token_ids=["a"])
    v0 = client.tokens_version
    client.update_tokens(["a"])
    assert client.tokens_version == v0
    client.update_tokens(["b", "a"])
    assert client.tokens_version == v0 + 1

    payload = json.loads(client.subscription_payload())
    assert payload["type"] == "market"
    assert sorted(payload["assets_ids"]) == ["a", "b"]


def test_run_direct_subscribes_pings_and_buffers_trades():
    """A full direct-socket pass: subscribe frame, PING keepalive, trade capture."""
    ws = FakeWS([_trade_frame("tok_up", "0.46", "30")])
    connector = FakeConnector([ws])
    client = CLOBMarketWSClient(token_ids=["tok_up"], connect_factory=connector,
                                ping_interval=0.01)

    async def drive():
        task = asyncio.create_task(client.run_direct())
        for _ in range(200):
            await asyncio.sleep(0.01)
            if client.drain_ready and len(ws.sent) >= 2:
                break
        client.stop()
        await asyncio.wait_for(task, timeout=5.0)

    asyncio.run(drive())

    assert connector.calls and connector.calls[0][0] == CLOB_WS_URL
    sub = json.loads(ws.sent[0])
    assert sub["assets_ids"] == ["tok_up"]
    assert "PING" in ws.sent[1:], f"no PING keepalive sent: {ws.sent}"
    drained = client.drain_trades("tok_up")
    assert len(drained) == 1 and drained[0]["price"] == 0.46


def test_run_direct_reconnects_after_socket_error():
    """A dropped socket is retried and the reconnect counter advances."""
    first = FakeWS([_trade_frame("tok_up", "0.40", "5")], fail_after=True)
    second = FakeWS([_trade_frame("tok_up", "0.41", "6")])
    connector = FakeConnector([first, second])
    client = CLOBMarketWSClient(token_ids=["tok_up"], connect_factory=connector,
                                ping_interval=5.0, backoff_base=0.01,
                                backoff_max=0.02)

    async def drive():
        task = asyncio.create_task(client.run_direct())
        for _ in range(300):
            await asyncio.sleep(0.01)
            if len(connector.calls) >= 2 and client.trades_captured >= 2:
                break
        client.stop()
        await asyncio.wait_for(task, timeout=5.0)

    asyncio.run(drive())

    assert len(connector.calls) >= 2
    assert client.reconnect_count >= 1
    prices = sorted(t["price"] for t in client.drain_trades("tok_up"))
    assert prices == [0.40, 0.41]


def test_drain_trades_is_thread_safe():
    """Concurrent producers and drainers never lose or duplicate a print."""
    client = CLOBMarketWSClient()
    produced = 400
    stop = threading.Event()
    drained: list[dict] = []

    def produce():
        for i in range(produced):
            client.handle_raw_message(_trade_frame("tok", "0.5", "1", tx=f"0x{i}"))

    def drain():
        while not stop.is_set():
            drained.extend(client.drain_trades("tok"))

    producers = [threading.Thread(target=produce) for _ in range(3)]
    drainer = threading.Thread(target=drain)
    drainer.start()
    for p in producers:
        p.start()
    for p in producers:
        p.join()
    stop.set()
    drainer.join(timeout=5.0)
    drained.extend(client.drain_trades("tok"))

    assert len(drained) == produced * 3


# --------------------------------------------------------------------------
# 3. Bridge
# --------------------------------------------------------------------------

def test_bridge_lifecycle_and_status():
    """The bridge runs the client on a daemon thread and stops cleanly."""
    ws = FakeWS([_trade_frame("tok_up", "0.45", "12")])
    bridge = CLOBStreamCollectorBridge(token_ids=["tok_up"],
                                       connect_factory=FakeConnector([ws]),
                                       ping_interval=0.05)
    bridge.start()
    try:
        deadline = time.time() + 5.0
        while time.time() < deadline and not bridge.is_connected:
            time.sleep(0.02)
        assert bridge.is_connected is True
        status = bridge.get_status()
        assert status["ws_connected"] is True
        assert status["token_count"] == 1
        assert status["reconnects"] == 0
    finally:
        bridge.stop()
    assert bridge.is_running is False
    assert bridge.get_status()["ws_connected"] is False


def test_bridge_drains_trades_and_books_across_threads():
    """Drain, book and token-update calls are safe from the caller thread."""
    ws = FakeWS([
        _trade_frame("tok_up", "0.44", "9"),
        json.dumps({"event_type": "book", "asset_id": "tok_up",
                    "bids": [{"price": "0.43", "size": "10"}],
                    "asks": [{"price": "0.45", "size": "10"}]}),
    ])
    bridge = CLOBStreamCollectorBridge(token_ids=["tok_up"],
                                       connect_factory=FakeConnector([ws]),
                                       ping_interval=0.05)
    bridge.start()
    try:
        deadline = time.time() + 5.0
        while time.time() < deadline and not bridge.get_book_for_token("tok_up"):
            time.sleep(0.02)
        trades = bridge.drain_trades_for_token("tok_up")
        assert len(trades) == 1 and trades[0]["size"] == 9.0
        book = bridge.get_book_for_token("tok_up")
        assert book["best_bid"] == 0.43 and book["best_ask"] == 0.45
        assert bridge.get_book_for_token("nope") is None

        bridge.update_subscribed_tokens(["tok_up", "tok_dn"])
        assert bridge.get_status()["token_count"] == 2
    finally:
        bridge.stop()


def test_bridge_stop_is_idempotent():
    """Stopping a bridge twice (or one never started) is a no-op, not a crash."""
    bridge = CLOBStreamCollectorBridge(token_ids=["t"],
                                       connect_factory=FakeConnector([]))
    bridge.stop()
    bridge.start()
    bridge.stop()
    bridge.stop()
    assert bridge.is_running is False


# --------------------------------------------------------------------------
# 4. collect_ticks integration
# --------------------------------------------------------------------------

class StubBridge:
    """Minimal bridge stand-in for collector integration tests."""

    def __init__(self, trades: dict[str, list[dict]], connected: bool = True):
        self.trades = {k: list(v) for k, v in trades.items()}
        self._connected = connected
        self.subscribed: list[str] = []
        self.reconnects = 0

    @property
    def is_connected(self) -> bool:
        return self._connected

    def update_subscribed_tokens(self, tokens):
        self.subscribed = list(tokens)

    def drain_trades_for_token(self, token_id):
        return self.trades.pop(token_id, [])

    def get_status(self):
        return {"ws_connected": self._connected, "reconnects": self.reconnects,
                "token_count": len(self.subscribed), "trades_captured": 0}


@pytest.fixture()
def collector(monkeypatch):
    """`scripts.collect_ticks` with gamma/book/tape network calls stubbed out."""
    import scripts.collect_ticks as ct

    ct.windows.clear()
    monkeypatch.setattr(ct, "SERIES", [("btc-up-or-down-5m", 300, "BTC 5m")])
    monkeypatch.setattr(ct, "fetch_live_for_series", lambda slug: ({
        "conditionId": "0xCID", "slug": "btc-updown-5m-1",
        "start_ts": time.time() - 10.0, "end_ts": time.time() + 300.0,
        "up_token": "tok_up", "down_token": "tok_dn", "series": slug,
    }, None))
    monkeypatch.setattr(ct, "full_book", lambda host, tok: {
        "bids": {0.48: 100.0}, "asks": {0.52: 100.0},
        "best_bid": 0.48, "best_ask": 0.52, "malformed": 0, "token_id": tok,
    })
    monkeypatch.setattr(ct, "recent_trades", lambda cid, seen, limit=200: {})
    yield ct
    ct.windows.clear()


def _read_snaps(out_dir: Path) -> list[dict]:
    files = sorted(out_dir.glob("ticks_*.jsonl"))
    rows: list[dict] = []
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def test_poll_once_drains_ws_trades_into_tape_delta(collector, tmp_path):
    """Streamed prints land in `tape_delta` with the schema the engine consumes."""
    bridge = StubBridge({
        "tok_up": [{"asset": "tok_up", "price": 0.46, "size": 30.0,
                    "side": "BUY", "ts": 1700000000000, "hash": "0xa"}],
        "tok_dn": [{"asset": "tok_dn", "price": 0.50, "size": 12.0,
                    "side": "SELL", "ts": 1700000000000, "hash": "0xb"}],
    })
    stats: dict = {}
    collector.poll_once(tmp_path, False, stats, ws_bridge=bridge)

    snaps = _read_snaps(tmp_path)
    assert len(snaps) == 1
    tape = snaps[0]["tape_delta"]
    assert len(tape) == 2
    for t in tape:
        assert set(t) >= {"asset", "price", "size"}
        assert isinstance(t["price"], float) and isinstance(t["size"], float)
    assert sorted(bridge.subscribed) == ["tok_dn", "tok_up"]
    assert stats["tape_captured_ws"] == 2
    assert stats["ws_connected"] is True


def test_poll_once_deduplicates_repeated_ws_prints(collector, tmp_path):
    """The same print delivered twice by the socket is written once."""
    dup = {"asset": "tok_up", "price": 0.46, "size": 30.0, "side": "BUY",
           "ts": 1700000000000, "hash": "0xa"}
    bridge = StubBridge({"tok_up": [dict(dup), dict(dup)]})
    stats: dict = {}
    collector.poll_once(tmp_path, False, stats, ws_bridge=bridge)

    tape = _read_snaps(tmp_path)[0]["tape_delta"]
    assert len(tape) == 1
    assert stats["tape_captured_ws"] == 1


def test_poll_once_falls_back_to_rest_when_ws_disconnected(collector, tmp_path,
                                                          monkeypatch):
    """A dead socket must not starve the tape — REST takes over silently."""
    monkeypatch.setattr(
        collector, "recent_trades",
        lambda cid, seen, limit=200: {"tok_up": {0.45: 77.0}},
    )
    bridge = StubBridge({}, connected=False)
    stats: dict = {}
    collector.poll_once(tmp_path, False, stats, ws_bridge=bridge)

    tape = _read_snaps(tmp_path)[0]["tape_delta"]
    assert tape == [{"asset": "tok_up", "price": 0.45, "size": 77.0}]
    assert stats["tape_captured_rest"] == 1
    assert stats["ws_connected"] is False


def test_poll_once_without_bridge_uses_rest_only(collector, tmp_path, monkeypatch):
    """`--no-ws` (no bridge at all) keeps the legacy REST behaviour intact."""
    monkeypatch.setattr(
        collector, "recent_trades",
        lambda cid, seen, limit=200: {"tok_dn": {0.55: 4.0}},
    )
    stats: dict = {}
    collector.poll_once(tmp_path, False, stats)

    tape = _read_snaps(tmp_path)[0]["tape_delta"]
    assert tape == [{"asset": "tok_dn", "price": 0.55, "size": 4.0}]
    assert stats.get("ws_connected") is False


def test_rest_fallback_does_not_replay_a_price_the_socket_already_printed(
        collector, tmp_path, monkeypatch):
    """Cross-source TTL dedup: WS print at a level suppresses the REST echo."""
    bridge = StubBridge({
        "tok_up": [{"asset": "tok_up", "price": 0.46, "size": 30.0,
                    "side": "BUY", "ts": 1700000000000, "hash": "0xa"}],
    })
    stats: dict = {}
    collector.poll_once(tmp_path, False, stats, ws_bridge=bridge)

    # Socket dies; REST now reports the very same price level plus a new one.
    bridge._connected = False
    monkeypatch.setattr(
        collector, "recent_trades",
        lambda cid, seen, limit=200: {"tok_up": {0.46: 30.0, 0.44: 9.0}},
    )
    collector.poll_once(tmp_path, False, stats, ws_bridge=bridge)

    snaps = _read_snaps(tmp_path)
    assert len(snaps) == 2
    assert snaps[1]["tape_delta"] == [{"asset": "tok_up", "price": 0.44, "size": 9.0}]


def test_manifest_carries_ws_telemetry(tmp_path):
    """Manifest exposes socket health for the dashboard and the audit trail."""
    import scripts.collect_ticks as ct

    stats = {"lines": 3, "ws_enabled": True, "ws_connected": True,
             "ws_reconnects": 2, "tape_captured_ws": 41,
             "tape_captured_rest": 5, "_tape_window": []}
    ct.update_manifest(tmp_path, stats)
    data = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert data["ws_enabled"] is True
    assert data["ws_connected"] is True
    assert data["ws_reconnects"] == 2
    assert data["tape_captured_ws"] == 41
    assert data["tape_captured_rest"] == 5
    assert "_tape_window" not in data


# --------------------------------------------------------------------------
# 5. Replay proof — streamed tape actually fills
# --------------------------------------------------------------------------

def test_streamed_tape_produces_fills_in_replay():
    """Ticks carrying WS-sourced `tape_delta` fill under `fill_model="tape"`."""
    from backtest.engine import BacktestParams, replay

    start = 1700000000.0
    params = BacktestParams(fill_model="tape", offset=0.02)

    def snap(i: int, tape: list[dict]) -> dict:
        ts = start + i
        book_up = {"bids": {0.48: 500.0}, "asks": {0.52: 500.0},
                   "best_bid": 0.48, "best_ask": 0.52, "malformed": 0,
                   "token_id": "tok_up"}
        book_dn = {"bids": {0.48: 500.0}, "asks": {0.52: 500.0},
                   "best_bid": 0.48, "best_ask": 0.52, "malformed": 0,
                   "token_id": "tok_dn"}
        return {
            "ts": ts, "iso": "", "series": "btc-up-or-down-5m", "duration": 300,
            "label": "BTC 5m", "cid": "0xCID", "slug": "btc-updown-5m-1",
            "start_ts": start, "end_ts": start + 300.0,
            "t_rem": start + 300.0 - ts,
            "up_token": "tok_up", "down_token": "tok_dn",
            "up_book": book_up, "down_book": book_dn,
            "tape_delta": tape, "mid": 0.50, "touch_pair": 1.04,
            "resting_pair": 0.96, "queue_up": 0.0, "queue_down": 0.0,
            "err": None,
        }

    fill_up = [{"asset": "tok_up", "price": 0.48, "size": 50.0}]
    fill_dn = [{"asset": "tok_dn", "price": 0.48, "size": 50.0}]
    snaps = [snap(0, []), snap(1, fill_up), snap(2, fill_dn)]
    snaps += [snap(i, []) for i in range(3, 40)]

    streamed = replay(snaps, params)
    assert streamed["n_windows"] == 1
    w = streamed["per_window"][0]
    assert w["filled_up"] is True and w["filled_down"] is True
    assert w["pair_captured"] is True

    starved = replay([snap(i, []) for i in range(40)], params)
    sw = starved["per_window"][0]
    assert sw["filled_up"] is False and sw["filled_down"] is False
