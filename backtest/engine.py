"""SPREAD-2 backtest engine.

Pure function `replay(snaps, params) -> results` that consumes a chronological
list of tick dicts (the same shape written by `scripts/collect_ticks.py`) and
returns per-window P&L plus aggregate stats. Zero network calls.

Per Plan §2 + D3 (dir/range input, group by cid across files) + D6 (engine
importable, CLI thin wrapper) + D8 (cid index for fast slider sweep):

  snaps = load_ticks("run/ticks/")  # iterates all *.jsonl[.gz]
  for cid, window_snaps in group_by_cid(snaps):  # handles midnight split
      ...
  params = BacktestParams(offset=0.02, queue_gate=50, exit_thresh_by_slug={...},
                         fill_model="tape", ...)
  results = replay(snaps, params)

`fill_model="tape"` is the conservative default (matches `strategy/markets.py:271`
- only counts a trade that the venue actually printed). `"book"` is optimistic
(book crossed our resting price = filled). `"both"` reports both so the UI
can show the gap.
"""
from __future__ import annotations
import gzip
import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Iterable, Iterator

# --- loaders ----------------------------------------------------------------

def _open_text(path: Path) -> Iterator[str]:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                yield line
    else:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                yield line


def load_ticks(source) -> Iterator[dict | None]:
    """Yield tick dicts from a file, directory of tick files, or list of paths.

    `.jsonl` and `.jsonl.gz` are both supported. Each non-blank line must be
    a JSON object. Blank or malformed lines yield ``None`` (skipped by
    :func:`iter_ticks`, the safe entry point for callers that need only valid
    dicts). This preserves the existing skip behavior without changing callers.
    """
    if isinstance(source, (str, Path)):
        path = Path(source)
        if path.is_dir():
            files = sorted(p for p in path.iterdir()
                           if p.is_file() and p.suffix in (".jsonl", ".gz"))
            for f in files:
                yield from load_ticks(f)
            return
        yield from (_json_or_skip(line) for line in _open_text(path))
        return
    if isinstance(source, list):
        for p in source:
            yield from load_ticks(p)
        return
    raise TypeError(f"unsupported source: {type(source).__name__}")


def _json_or_skip(line: str):
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except Exception:
        return None


def iter_ticks(source) -> Iterator[dict]:
    """Like load_ticks but drops None (skips bad lines)."""
    for s in load_ticks(source):
        if s is not None:
            yield s


def group_by_cid(snaps: Iterable[dict]) -> list[tuple[str, list[dict]]]:
    """Group a chronological snap stream by cid, sorted by ts within each group.

    Handles midnight windows that straddle two daily files (Plan D3): cids
    appear in whatever file they were sampled, then are re-sorted by ts.
    """
    out: dict[str, list[dict]] = defaultdict(list)
    for s in snaps:
        cid = s.get("cid")
        if not cid:
            continue
        out[cid].append(s)
    grouped = [(cid, sorted(snaps, key=lambda x: x.get("ts", 0.0)))
               for cid, snaps in out.items()]
    grouped.sort(key=lambda kv: kv[1][0].get("ts", 0.0))
    return grouped


# --- parameters -------------------------------------------------------------

@dataclass(frozen=True)
class BacktestParams:
    """All knobs the engine consumes. Frozen = deterministic replay."""
    offset: float = 0.020
    queue_gate: float = 50.0          # 0 disables
    pair_cost_gate: float = 0.995
    exit_thresh_by_slug: dict = field(default_factory=lambda: {
        "btc-up-or-down-5m": 0.09, "sol-up-or-down-5m": 0.11,
        "btc-up-or-down-15m": 0.13, "sol-up-or-down-15m": 0.13,
        "default_5m": 0.12, "default_15m": 0.13,
    })
    exit_reversal: float = 0.02
    quote_shares: int = 120
    fill_model: str = "tape"         # "tape" | "book" | "both"
    tick_size: float = 0.001
    merge_gas_usd: float = 0.05
    taker_fee_rate: float = 0.07     # crypto fee coefficient
    min_quote_shares: int = 50

    def exit_thresh(self, slug: str, duration: int) -> float:
        if slug in self.exit_thresh_by_slug:
            return float(self.exit_thresh_by_slug[slug])
        key = f"default_{'5m' if duration == 300 else '15m'}"
        return float(self.exit_thresh_by_slug.get(key, 0.12))

    def params_hash(self) -> str:
        """Stable hash for cache keying slider sweeps (Plan D8)."""
        payload = json.dumps(asdict(self), sort_keys=True, default=str)
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


# --- simulation -------------------------------------------------------------

@dataclass
class WindowResult:
    cid: str
    series: str
    slug: str
    duration: int
    n_snaps: int
    class_label: str
    max_up: float
    max_down: float
    filled_up: bool
    filled_down: bool
    pair_captured: bool
    exit_taken: bool
    exit_side: str                   # "up" | "down" | ""
    pnl_cents: float                 # +4 per pair, -exit_cost per naked, -gas share
    fees_cents: float
    err: str = ""


def _mid(book: dict):
    bb, ba = book.get("best_bid"), book.get("best_ask")
    if bb is not None and ba is not None:
        return (bb + ba) / 2.0
    if bb is not None:
        return bb + 0.005
    if ba is not None:
        return ba - 0.005
    return None


def _taker_fee(p: float, rate: float) -> float:
    if p is None or p <= 0 or p >= 1:
        return 0.0
    return rate * p * (1.0 - p)


def _classify(mids: list[float]) -> str:
    if not mids:
        return "no_data"
    max_up = max(mids) - 0.50
    max_down = 0.50 - min(mids)
    if max_up >= 0.02 and max_down >= 0.02:
        return "oscillating"
    if max_up >= 0.02 or max_down >= 0.02:
        return "monotonic"
    return "flat"


def _simulate_window(window_snaps: list[dict], params: BacktestParams) -> WindowResult:
    if not window_snaps:
        return WindowResult("", "", "", 0, 0, "no_data", 0.0, 0.0,
                            False, False, False, False, "", 0.0, 0.0, "empty")

    first = window_snaps[0]
    cid = first.get("cid", "")
    series = first.get("series", "")
    slug = first.get("slug", "")
    duration = int(first.get("duration", 0))

    filled_up = False
    filled_down = False
    exit_taken = False
    exit_side = ""
    pair_captured = False
    mids: list[float] = []
    max_up = 0.0
    max_down = 0.0
    reversal_seen_up = False          # mid came back toward 0.50 after excursion
    reversal_seen_down = False
    pnl_cents = 0.0
    fees_cents = 0.0
    err = ""

    exit_thr = params.exit_thresh(slug, duration)

    for s in window_snaps:
        ub = s.get("up_book") or {}
        db = s.get("down_book") or {}
        mid = _mid(ub)
        if mid is None:
            continue
        mids.append(mid)
        if mid - 0.50 > max_up:
            max_up = mid - 0.50
        if 0.50 - mid > max_down:
            max_down = 0.50 - mid

        # "reversal_seen_<side>" = mid has come back toward 0.50 after exceeding
        # exit_thr on the opposite side -- the current drift is no longer
        # monotonic, so don't exit. E.g. if max_down >= exit_thr and then mid
        # is now back within exit_reversal of 0.50, the down excursion was a
        # round-trip and the up drift is unsurprising.
        if max_down >= exit_thr and (0.50 - mid) < params.exit_reversal:
            reversal_seen_up = True
        if max_up >= exit_thr and (mid - 0.50) < params.exit_reversal:
            reversal_seen_down = True

        resting_up = round(mid - params.offset, 3)
        resting_down = round((1.0 - mid) - params.offset, 3)

        # Queue gate (0 disables per Plan §2; max_rest_queue_ahead=0 means "always pass")
        if params.queue_gate <= 0:
            queue_ok = True
        else:
            q_up = sum(sz for p, sz in (ub.get("bids") or {}).items()
                       if float(p) >= resting_up)
            q_dn = sum(sz for p, sz in (db.get("bids") or {}).items()
                       if float(p) >= resting_down)
            queue_ok = (q_up <= params.queue_gate) and (q_dn <= params.queue_gate)

        # Touch pair gate
        up_ask = ub.get("best_ask")
        dn_ask = db.get("best_ask")
        touch = None
        if up_ask is not None and dn_ask is not None:
            touch = up_ask + dn_ask
        pair_cost_ok = (touch is None) or (touch <= params.pair_cost_gate)

        if not queue_ok or not pair_cost_ok:
            # An already-filled position must still be eligible to exit even
            # if the live book no longer meets the entry gate. Check exit
            # BEFORE updating the reversal flag, otherwise the crossing tick
            # sets the flag and the exit is suppressed.
            if (filled_up and not filled_down and max_up >= exit_thr
                    and not reversal_seen_down and not exit_taken):
                exit_taken = True
                exit_side = "down"
                bb_dn = db.get("best_bid") or 0.0
                pnl_cents -= (1.0 - bb_dn) * 100.0
                fees_cents += _taker_fee(1.0 - bb_dn, params.taker_fee_rate) * 100.0
            if (filled_down and not filled_up and max_down >= exit_thr
                    and not reversal_seen_up and not exit_taken):
                exit_taken = True
                exit_side = "up"
                bb_up = ub.get("best_bid") or 0.0
                pnl_cents -= bb_up * 100.0
                fees_cents += _taker_fee(bb_up, params.taker_fee_rate) * 100.0
            # Update reversal flags before skipping so mid drift isn't lost.
            if max_up >= exit_thr:
                reversal_seen_up = True
            if max_down >= exit_thr:
                reversal_seen_down = True
            continue

        # --- FILL DETECTION (Plan fill_model: tape conservative default) ---
        # Tape-confirmed: a real trade printed at our resting price.
        # Book-only: best_ask <= resting_price means book crossed us.
        # Hoist token lookups and guard empty identifiers (prevents "" == "" match).
        up_token = (first.get("up_token") or (ub.get("token_id") or "")).strip()
        dn_token = (first.get("down_token") or (db.get("token_id") or "")).strip()
        for trade in s.get("tape_delta") or []:
            tasset = str(trade.get("asset", "")).strip()
            if not tasset:
                continue
            tprice = float(trade.get("price", 0))
            if up_token and tasset == up_token and abs(tprice - resting_up) <= params.tick_size:
                if params.fill_model in ("tape", "both"):
                    filled_up = True
            if dn_token and tasset == dn_token and abs(tprice - resting_down) <= params.tick_size:
                if params.fill_model in ("tape", "both"):
                    filled_down = True
        if params.fill_model in ("book", "both"):
            if up_ask is not None and up_ask <= resting_up:
                filled_up = True
            if dn_ask is not None and dn_ask <= resting_down:
                filled_down = True

        # --- EXIT (one side filled, mid drifted past thresh without reversal) ---
        # Check BEFORE we update the reversal flag this tick so the crossing
        # tick is the exit tick (otherwise the flag toggles the same tick and
        # the exit is suppressed).
        if (filled_up and not filled_down and max_up >= exit_thr
                and not reversal_seen_down and not exit_taken):
            exit_taken = True
            exit_side = "down"
            bb_dn = db.get("best_bid") or 0.0
            pnl_cents -= (1.0 - bb_dn) * 100.0
            fees_cents += _taker_fee(1.0 - bb_dn, params.taker_fee_rate) * 100.0
        if (filled_down and not filled_up and max_down >= exit_thr
                and not reversal_seen_up and not exit_taken):
            exit_taken = True
            exit_side = "up"
            bb_up = ub.get("best_bid") or 0.0
            pnl_cents -= bb_up * 100.0
            fees_cents += _taker_fee(bb_up, params.taker_fee_rate) * 100.0

        # "reversal_seen_<side>" = the OTHER side has also hit >= exit_thr at some
        # point, so this drift isn't a one-sided surprise -- don't exit next tick.
        if max_down >= exit_thr:
            reversal_seen_up = True
        if max_up >= exit_thr:
            reversal_seen_down = True

        # --- PAIR COMPLETION ---
        if filled_up and filled_down and not pair_captured:
            pair_captured = True
            pnl_cents += 4.0
            # merge_gas_usd is a per-transaction cost; amortize over the
            # actual shares in the pair so pnl_cents stays per-share.
            pnl_cents -= (params.merge_gas_usd * 100.0) / max(1, params.quote_shares)

    if filled_up or filled_down:
        if (filled_up and not filled_down) or (filled_down and not filled_up):
            fees_cents += _taker_fee(0.50, params.taker_fee_rate) * 100.0
        # Mark any still-open naked leg to the final observed price so the
        # window's P&L reflects settlement. Use the last snap's best_bid for
        # the held side (executable quote). Charge the taker fee only on the
        # actual close; preserve pair-capture and exit behavior above.
        if not pair_captured and not exit_taken:
            last = window_snaps[-1] if window_snaps else {}
            lb = (last.get("up_book") or {}).get("best_bid")
            db_bid = (last.get("down_book") or {}).get("best_bid")
            if filled_up and not filled_down and lb is not None:
                pnl_cents -= lb * 100.0
                fees_cents += _taker_fee(lb, params.taker_fee_rate) * 100.0
            elif filled_down and not filled_up and db_bid is not None:
                # DOWN leg held: value is 1 - mid, approximated by down best_bid
                pnl_cents -= db_bid * 100.0
                fees_cents += _taker_fee(db_bid, params.taker_fee_rate) * 100.0

    return WindowResult(
        cid=cid, series=series, slug=slug, duration=duration,
        n_snaps=len(window_snaps),
        class_label=_classify(mids),
        max_up=round(max_up, 4), max_down=round(max_down, 4),
        filled_up=filled_up, filled_down=filled_down,
        pair_captured=pair_captured, exit_taken=exit_taken, exit_side=exit_side,
        pnl_cents=round(pnl_cents, 4),
        fees_cents=round(fees_cents, 4),
        err=err,
    )


def replay(snaps: Iterable[dict], params: BacktestParams) -> dict:
    """Replay all snaps, return aggregate + per-window results.

    Output schema:
      {
        "params_hash": "...",
        "n_snaps": int, "n_windows": int,
        "per_window": [WindowResult, ...],
        "aggregate": {
            "per_series": {slug: {windows, pair_rate, exit_rate, ...}},
            "overall":   {windows, pair_rate, exit_rate, total_pnl_cents, ...}
        }
      }
    """
    snaps_list = list(snaps)
    n_snaps = len(snaps_list)
    per_window: list[WindowResult] = []
    for _cid, group in group_by_cid(snaps_list):
        per_window.append(_simulate_window(group, params))

    per_series: dict[str, dict] = defaultdict(lambda: {
        "windows": 0, "pair": 0, "exit": 0, "filled_up_only": 0,
        "filled_down_only": 0, "oscillating": 0, "monotonic": 0, "flat": 0,
        "total_pnl_cents": 0.0, "total_fees_cents": 0.0,
        "wins": 0, "peak_pnl": 0.0, "cum_pnl": 0.0, "max_dd": 0.0,
    })

    cum_pnl = 0.0
    peak_pnl = 0.0
    max_dd = 0.0
    equity_curve: list[dict] = []
    trades_sample: list[dict] = []
    wins_count = 0

    for i, w in enumerate(per_window):
        # Global equity curve and max drawdown
        cum_pnl += w.pnl_cents
        if cum_pnl > peak_pnl:
            peak_pnl = cum_pnl
        dd = peak_pnl - cum_pnl
        if dd > max_dd:
            max_dd = dd
        if w.pnl_cents > 0:
            wins_count += 1

        equity_curve.append({
            "window": i + 1,
            "pnl": round(cum_pnl, 2),
            "slug": w.slug,
            "series": w.series,
        })

        if len(trades_sample) < 50:
            exit_info = f"exit_{w.exit_side}" if w.exit_taken else ("pair_merged" if w.pair_captured else "-")
            trades_sample.append({
                "slug": w.slug,
                "series": w.series,
                "both_filled": w.pair_captured,
                "exit_triggered": w.exit_taken,
                "up_filled": w.filled_up,
                "down_filled": w.filled_down,
                "pnl_cents": round(w.pnl_cents, 2),
                "exit_reason": exit_info,
            })

        # Per series tracking
        a = per_series[w.series]
        a["windows"] += 1
        if w.pair_captured:
            a["pair"] += 1
        if w.exit_taken:
            a["exit"] += 1
        if w.filled_up and not w.filled_down:
            a["filled_up_only"] += 1
        if w.filled_down and not w.filled_up:
            a["filled_down_only"] += 1
        if w.class_label == "oscillating":
            a["oscillating"] += 1
        elif w.class_label == "monotonic":
            a["monotonic"] += 1
        elif w.class_label == "flat":
            a["flat"] += 1
        a["total_pnl_cents"] += w.pnl_cents
        a["total_fees_cents"] += w.fees_cents

        if w.pnl_cents > 0:
            a["wins"] += 1
        a["cum_pnl"] += w.pnl_cents
        if a["cum_pnl"] > a["peak_pnl"]:
            a["peak_pnl"] = a["cum_pnl"]
        s_dd = a["peak_pnl"] - a["cum_pnl"]
        if s_dd > a["max_dd"]:
            a["max_dd"] = s_dd

    def _finalize(d: dict) -> dict:
        n = d.get("windows", 0)
        return {
            "windows": n,
            "pair_rate": round(d.get("pair", 0) / n, 4) if n else 0.0,
            "exit_rate": round(d.get("exit", 0) / n, 4) if n else 0.0,
            "filled_up_only": d.get("filled_up_only", 0),
            "filled_down_only": d.get("filled_down_only", 0),
            "oscillating": d.get("oscillating", 0),
            "monotonic": d.get("monotonic", 0),
            "flat": d.get("flat", 0),
            "total_pnl_cents": round(d.get("total_pnl_cents", 0.0), 4),
            "avg_pnl_cents": round(d.get("total_pnl_cents", 0.0) / n, 4) if n else 0.0,
            "total_fees_cents": round(d.get("total_fees_cents", 0.0), 4),
            "max_drawdown_cents": round(d.get("max_dd", 0.0), 2),
            "win_rate": round(d.get("wins", 0) / n, 4) if n else 0.0,
        }

    overall = {
        "windows": sum(s["windows"] for s in per_series.values()),
        "pair": sum(s["pair"] for s in per_series.values()),
        "exit": sum(s["exit"] for s in per_series.values()),
        "total_pnl_cents": sum(s["total_pnl_cents"] for s in per_series.values()),
        "total_fees_cents": sum(s["total_fees_cents"] for s in per_series.values()),
        "wins": wins_count,
        "max_dd": max_dd,
    }
    return {
        "params_hash": params.params_hash(),
        "n_snaps": n_snaps,
        "n_windows": len(per_window),
        "per_window": [asdict(w) for w in per_window],
        "equity_curve": equity_curve,
        "trades_sample": trades_sample,
        "aggregate": {
            "per_series": {k: _finalize(v) for k, v in per_series.items()},
            "overall": _finalize(overall),
        },
    }
