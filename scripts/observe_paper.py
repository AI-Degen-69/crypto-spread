"""Paper-run observer: journal live cockpit state for later strategy analysis.

Polls GET /api/live/state on the dashboard (default 127.0.0.1:8802) every
POLL_SEC seconds and appends two files under run/observations/:

  obs_YYYY-MM-DD.jsonl  - one snapshot per second: per-market quote/mid/fill/
                          reentry state, engine params, P&L totals.
  trades_YYYY-MM-DD.jsonl - deduped copy of every TradeEvent seen (the engine
                          also persists these to run/live_trades.jsonl, but the
                          observer's copy is timestamped at detection time and
                          survives dashboard resets).

Run alongside the dashboard while the paper bot is running:

    python -m scripts.observe_paper                 # foreground
    python -m scripts.observe_paper --summary       # print session stats and exit

Snapshots are intentionally small (a few hundred bytes/s) so a full day is
well under 50 MB. The engine's /api/live/state is read-only and cached-free;
polling it at 1s does not disturb the trading loop.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Set

BASE_URL = "http://127.0.0.1:8802"
OBS_DIR = Path("run") / "observations"
POLL_SEC = 1.0

# Market-state fields worth journaling per snapshot (compact = cheap to keep).
MKT_FIELDS = (
    "mid", "resting_up", "resting_down", "filled_up", "filled_down",
    "fill_price_up", "fill_price_down", "pair_captured", "pairs_count",
    "stops_count", "realized_pnl_usd", "unrealized_pnl_usd",
    "reentries", "entry_pending_since", "last_action", "up_bid", "up_ask",
    "down_bid", "down_ask", "spot_price", "spot_drift",
)


def fetch_state() -> Dict[str, Any]:
    """GET the live-trader state snapshot from the dashboard API."""
    with urllib.request.urlopen(f"{BASE_URL}/api/live/state", timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def today_stamp() -> str:
    """Today's date as an ISO stamp, used for journal filenames."""
    return datetime.date.today().isoformat()


def compact_market(m: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce a market state dict to the tracked MKT_FIELDS, dropping empties."""
    out: Dict[str, Any] = {}
    for k in MKT_FIELDS:
        v = m.get(k)
        if v is not None and v != "" and v != 0:
            out[k] = v
    return out


def snapshot_payload(state: Dict[str, Any]) -> Dict[str, Any]:
    """Build the compact per-poll journal record from a full state snapshot."""
    return {
        "ts": time.time(),
        "is_running": state.get("is_running"),
        "mode": state.get("mode"),
        "pnl": {
            "total": state.get("total_pnl"),
            "realized": state.get("realized_pnl"),
            "unrealized": state.get("unrealized_pnl"),
            "portfolio": state.get("portfolio_value"),
            "win_rate": state.get("win_rate"),
            "trades": state.get("total_trades"),
            "pairs_merged": state.get("pairs_merged"),
            "stops_triggered": state.get("stops_triggered"),
            "active_exposure": state.get("active_exposure"),
        },
        "params": state.get("params"),
        "markets": {
            slug: compact_market(m)
            for slug, m in (state.get("markets") or {}).items()
        },
        "reentry_stats": state.get("reentry_stats"),
    }


def trade_fingerprint(t: Dict[str, Any]) -> str:
    """Stable dedupe key for a trade event so restarts don't double-log."""
    return f"{t.get('id')}|{t.get('timestamp')}|{t.get('action')}|{t.get('pnl_usd')}"


def write_jsonl(path: Path, record: Dict[str, Any]) -> None:
    """Append one JSON record as a line to path, creating parents as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def summarize(obs_dir: Path) -> None:
    """Aggregate all observed trades and snapshots into a per-slug report."""
    trades: Dict[str, list] = defaultdict(list)
    for f in sorted(obs_dir.glob("trades_*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                t = json.loads(line)
                trades[t.get("slug", "?")].append(t)

    if not trades:
        print("No trades observed yet.")
        return

    print(f"\n{'slug':<32} {'n':>4} {'win%':>6} {'pairs':>6} {'stops':>6} {'pnl$':>9}")
    print("-" * 70)
    all_pnl = []
    for slug in sorted(trades):
        ts = trades[slug]
        pnl = sum(t.get("pnl_usd") or 0.0 for t in ts)
        wins = sum(1 for t in ts if (t.get("pnl_usd") or 0.0) > 0)
        pairs = sum(1 for t in ts if t.get("action") == "PAIR_MERGE")
        stops = sum(1 for t in ts if str(t.get("action", "")).startswith("STOP"))
        all_pnl.append((pnl, [t.get("pnl_usd") or 0.0 for t in ts]))
        print(f"{slug:<32} {len(ts):>4} {wins / len(ts) * 100:>5.1f}% {pairs:>6} {stops:>6} {pnl:>+9.2f}")
    flat = [p for _, ps in all_pnl for p in ps]
    total = sum(flat)
    worst = min(flat) if flat else 0.0
    print("-" * 70)
    print(f"TOTAL trades={len(flat)}  pnl={total:+.2f}  worst={worst:+.2f}  avg={total / max(1, len(flat)):+.3f}")


def main() -> None:
    """Poll the dashboard cockpit state and journal snapshots/trades to run/observe/."""
    ap = argparse.ArgumentParser(description="Journal paper-run cockpit state")
    ap.add_argument("--summary", action="store_true", help="print aggregated stats and exit")
    ap.add_argument("--url", default=BASE_URL)
    args = ap.parse_args()

    obs_dir = OBS_DIR
    if args.summary:
        summarize(obs_dir)
        return

    globals()["BASE_URL"] = args.url
    seen: Set[str] = set()
    # Pre-load today's dedupe set so restarts don't double-log.
    t_path = obs_dir / f"trades_{today_stamp()}.jsonl"
    if t_path.exists():
        for line in t_path.read_text(encoding="utf-8").splitlines():
            try:
                seen.add(trade_fingerprint(json.loads(line)))
            except Exception:
                pass

    print(f"Observing {args.url}/api/live/state -> {obs_dir}/  (Ctrl+C to stop)")
    err_streak = 0
    while True:
        try:
            state = fetch_state()
            err_streak = 0
        except Exception as e:
            err_streak += 1
            if err_streak in (1, 30):
                print(f"[warn] state fetch failed x{err_streak}: {e}", file=sys.stderr)
            time.sleep(POLL_SEC)
            continue

        write_jsonl(obs_dir / f"obs_{today_stamp()}.jsonl", snapshot_payload(state))

        for t in reversed((state.get("trades") or [])[:20]):
            fp = trade_fingerprint(t)
            if fp not in seen:
                seen.add(fp)
                t["detected_ts"] = time.time()
                write_jsonl(obs_dir / f"trades_{today_stamp()}.jsonl", t)
                print(f"[trade] {t.get('timestamp')} {t.get('slug')} {t.get('action')} "
                      f"pnl={t.get('pnl_usd'):+.2f}  ({t.get('notes', '')[:60]})")

        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
