"""Full-depth tick collector for 5m/15m SPREAD-2 replay.

Fork of `scripts/measure_5m_oscillation.py:1-305`. Same 1-second poll cadence
and gamma discovery, but every snapshot persists the complete UP and DOWN
order books plus the trade-tape delta. Output is replay-grade jsonl that
`backtest.engine.replay` consumes offline.

Per-series failure is isolated (D2, D4):
- 0-80ms per-request jitter prevents synchronized 30-rps bursts that
  trigger venue 429s at window boundaries.
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
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
from strategy.markets import full_book, recent_trades
from strategy.series import SERIES

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "run" / "ticks"
DEFAULT_OUT.mkdir(parents=True, exist_ok=True)

GAMMA_HOST = "https://gamma-api.polymarket.com"
CLOB_HOST = "https://clob.polymarket.com"
TRADES_API = "https://data-api.polymarket.com/trades"

POLL_INTERVAL = 1.0
JITTER_SEC = 0.080
SPREAD_OFFSET = 0.02
TAPE_LIMIT = 200
TICK_BUDGET_MS = 2000.0

# Per-cid state: { cid: {series, slug, start_ts, end_ts, up_token, down_token,
#                         seen_tape, snap_count, label, duration, jitter_jitter} }
windows: dict[str, dict] = {}


SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "Mozilla/5.0"})
SESSION.mount("https://", requests.adapters.HTTPAdapter(
    pool_connections=10, pool_maxsize=10, max_retries=0))
SESSION.mount("http://", requests.adapters.HTTPAdapter(
    pool_connections=4, pool_maxsize=4, max_retries=0))


def iso_to_unix(s: str) -> float:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s).timestamp()


def fetch_live_for_series(series_slug: str):
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


def write_snap(line: dict, out_dir: Path, day_key: str, gzip: bool) -> str:
    """Append one tick line to run/ticks/ticks_<day>.jsonl[.gz]; returns path."""
    suffix = ".jsonl.gz" if gzip else ".jsonl"
    path = out_dir / f"ticks_{day_key}{suffix}"
    payload = (json.dumps(line) + "\n").encode("utf-8")
    if gzip:
        import gzip
        with open(path, "ab") as f:
            f.write(gzip.compress(payload))
    else:
        with open(path, "ab") as f:
            f.write(payload)
    return str(path)


def update_manifest(out_dir: Path, stats: dict) -> None:
    (out_dir / "manifest.json").write_text(
        json.dumps({"ts": time.time(), **stats}, indent=2),
        encoding="utf-8",
    )


def compute_mid(book: dict):
    bb, ba = book.get("best_bid"), book.get("best_ask")
    if bb is not None and ba is not None:
        return (bb + ba) / 2.0
    if bb is not None:
        return bb + 0.005
    if ba is not None:
        return ba - 0.005
    return None


def queue_ahead(bids: dict, resting_price: float) -> float:
    if not bids:
        return 0.0
    return sum(s for p, s in bids.items() if p >= resting_price)


def now_day_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def poll_once(out_dir: Path, gzip: bool, stats: dict) -> tuple[list[str], list[str]]:
    """One poll across all 10 series. Returns (closed_window_slugs, errors)."""
    now = time.time()
    day_key = now_day_key()
    iso_now = datetime.fromtimestamp(now, tz=timezone.utc).isoformat()
    tick_start = time.perf_counter()
    closed: list[str] = []
    errs: list[str] = []

    for series_slug, duration, label in SERIES:
        info, err = fetch_live_for_series(series_slug)
        if not info:
            errs.append(f"{series_slug}:{err}")
            continue
        cid = info["conditionId"]
        if cid not in windows:
            windows[cid] = {
                "series": series_slug, "slug": info["slug"],
                "start_ts": info["start_ts"], "end_ts": info["end_ts"],
                "up_token": info["up_token"], "down_token": info["down_token"],
                "seen_tape": set(), "snap_count": 0,
                "label": label, "duration": duration,
            }
        w = windows[cid]
        time.sleep(random.uniform(0.0, JITTER_SEC))

        # Per-call isolation: one CLOB hiccup must not kill the collector
        # (Plan D2 hardening — earlier version let ReadTimeout propagate
        # to main() and exit the process after 4h of work).
        try:
            ub = full_book(CLOB_HOST, w["up_token"])
            ub_err = ub.get("err")
        except Exception as e:
            ub = {"bids": {}, "asks": {}, "best_bid": None,
                  "best_ask": None, "malformed": 0, "err": f"up:{e}"}
            ub_err = ub["err"]
        time.sleep(random.uniform(0.0, JITTER_SEC))
        try:
            db = full_book(CLOB_HOST, w["down_token"])
            db_err = db.get("err")
        except Exception as e:
            db = {"bids": {}, "asks": {}, "best_bid": None,
                  "best_ask": None, "malformed": 0, "err": f"down:{e}"}
            db_err = db["err"]
        time.sleep(random.uniform(0.0, JITTER_SEC))

        tape: list[dict] = []
        tape_err = ""
        try:
            tape_map = recent_trades(cid, w["seen_tape"], limit=TAPE_LIMIT)
            tape = list(tape_map.get(w["up_token"], {}).items())
        except Exception as e:
            tape_err = f"tape:{e}"
        tape_list = [{"asset": w["up_token"], "price": p, "size": s}
                     for p, s in tape]

        mid = compute_mid(ub)
        touch_pair = None
        if ub.get("best_ask") is not None and db.get("best_ask") is not None:
            touch_pair = ub["best_ask"] + db["best_ask"]
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
        if any(rr in snap for rr in ()):
            pass
        stats["lines"] = stats.get("lines", 0) + 1
        stats["series_seen"] = sorted(set(stats.get("series_seen", []) + [series_slug]))
        stats["day"] = day_key
        try:
            write_snap(snap, out_dir, day_key, gzip)
        except Exception as e:
            errs.append(f"write:{e}")
        w["snap_count"] += 1

    tick_ms = (time.perf_counter() - tick_start) * 1000.0
    if tick_ms > TICK_BUDGET_MS:
        errs.append(f"slow_tick:{tick_ms:.0f}ms")

    for cid, w in list(windows.items()):
        if w["end_ts"] < now:
            closed.append(w["slug"])
            del windows[cid]
    return closed, errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single poll then exit")
    ap.add_argument("--days", type=int, default=0, help="run until N UTC day boundaries pass")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output directory")
    ap.add_argument("--gzip", action="store_true", help="rotate daily file as .jsonl.gz")
    args = ap.parse_args()

    out_dir: Path = args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    stats: dict = {"lines": 0, "series_seen": []}

    print(f"collect_ticks: {len(SERIES)} series -> {out_dir}  gzip={args.gzip}")
    if args.once:
        closed, errs = poll_once(out_dir, args.gzip, stats)
        update_manifest(out_dir, stats)
        print(f"once done · closed={len(closed)} errs={len(errs)}")
        return

    day_boundaries = 0
    current_day = now_day_key()
    try:
        while True:
            closed, errs = poll_once(out_dir, args.gzip, stats)
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
            if int(time.time()) % 10 == 0:
                update_manifest(out_dir, stats)
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        print("interrupted")
    finally:
        update_manifest(out_dir, stats)


if __name__ == "__main__":
    main()
