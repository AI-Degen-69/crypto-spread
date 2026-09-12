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
from typing import Any, ClassVar, Iterable, Iterator

# --- loaders ----------------------------------------------------------------

def _open_text(path: Path) -> Iterator[str]:
    """Open a plain text or gzip file and yield lines."""
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
    """Parse JSON line into dict, or return None if empty/malformed/non-dict."""
    line = line.strip()
    if not line:
        return None
    try:
        val = json.loads(line)
        return val if isinstance(val, dict) else None
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
    queue_gate: float = 0.0           # 0 disables
    pair_cost_gate: float = 1.05
    exit_thresh_by_slug: dict = field(default_factory=lambda: {
        "btc-up-or-down-5m": 0.05, "sol-up-or-down-5m": 0.05,
        "btc-up-or-down-15m": 0.05, "sol-up-or-down-15m": 0.05,
        "default_5m": 0.05, "default_15m": 0.05,
    })
    exit_reversal: float = 0.02
    quote_shares: int = 5
    fill_model: str = "tape"         # "tape" | "book" | "both" | "cross"
    tick_size: float = 0.001
    merge_gas_usd: float = 0.0
    taker_fee_rate: float = 0.07     # crypto fee coefficient
    min_quote_shares: int = 5
    max_start_delay_sec: float = 0.0  # 0 disables; e.g. 5.0 filters late-start windows
    entry_timeout_pct: float = 0.10   # 0 disables; e.g. 0.10 cancels unfilled entry quotes once 10% elapsed
    # Late-start guard (issue #96), independent of entry_timeout_pct: a window whose
    # first snapshot already lands this far in was never observed at its open, so it
    # is neither entered nor used for the adverse-open snapshot. Mirrors
    # LiveTraderEngine.max_start_elapsed_pct. 0 disables.
    max_start_elapsed_pct: float = 0.10
    # Drift-skip re-entry (issue #95). A window the adverse-open gate skipped is
    # re-entered once the replay mid reverts within `reentry_drift_band` of 0.50
    # with at least `min_requote_remaining_sec` left to pair two legs. Only the
    # gate sets `adverse_skipped` in `_simulate_window`; entry-timeout and
    # late-start cancels are never re-entered. Defaults are byte-identical to
    # `LiveTraderEngine.__init__`. 0 disables the respective guard.
    reentry_drift_band: float = 0.015
    # Mirrors LiveTraderEngine.DEFAULT_MIN_REQUOTE_REMAINING_SEC (issue #89), which
    # issue #95 shares rather than defining a second knob with the same meaning. At
    # 300s a 5m window can never clear the gate, so only 15m windows re-enter until
    # an operator lowers it; the tests set it explicitly to exercise the rule.
    min_requote_remaining_sec: float = 300.0
    # Re-entry time gate as a fraction of the window, mirroring
    # LiveTraderEngine.reentry_min_remaining_pct. The effective gate is the tighter
    # of this and `min_requote_remaining_sec`, so a 5m replay needs 90s left and a
    # 15m one 270s. 0 or >= 1.0 falls back to the absolute knob alone.
    reentry_min_remaining_pct: float = 0.30
    # How many times one window may be recovered by re-entry. 1 keeps a market
    # oscillating across the band from thrashing the book for a whole window;
    # 0 disables re-entry outright. Mirrors LiveTraderEngine.
    max_reentries_per_window: int = 1
    # Patient undecided-band maker knobs (issue #145, mirrors issue #137 live
    # semantics). `entry_delay_sec` holds all quoting until that many seconds
    # into the window (0 = off); `entry_band` only admits windows whose
    # two-sided mid is still near 0.50 at entry time (0 = off). Defaults
    # preserve the current behavior exactly.
    entry_delay_sec: float = 0.0
    entry_band: float = 0.0

    # ── param grouping metadata ──────────────────────────────────────────────
    # Separates operator-controlled (live-replicable) knobs from execution
    # assumptions and internal window policy so the UI and API can render them
    # in distinct sections without touching any field names or the hash contract.
    # Each group lists (field_name, label, why). Unknown keys silently drop so new
    # fields don't break the grouping on a missing-entry error.
    _PARAM_GROUPS: ClassVar[dict[str, list[tuple[str, str, str]]]] = {
        "trading_knobs": [
            ("offset", "Spread Offset — where you rest", "You set this live on the book"),
            ("queue_gate", "Queue Depth — book depth filter", "You choose how many orders ahead to clear through"),
            ("pair_cost_gate", "Max Pair Cost ($) — cost ceiling", "Your cost threshold before walking away"),
            ("quote_shares", "Order Shares per Leg — position size", "Your sizing decision"),
            ("max_start_delay_sec", "Max Start Delay (s) — window filter", "You decide which windows are fresh enough to enter"),
            ("entry_delay_sec", "Entry Delay (s) — quote hold", "You hold quotes until the window matures"),
            ("entry_band", "Entry Band — undecided-market filter", "You admit only undecided markets at entry time"),
            ("exit_thresh_by_slug", "Exit Stop Loss Thresholds ($)", "Your stop placement — per series / duration"),
        ],
        "execution_assumptions": [
            ("fill_model", "Fill Model — execution assumption", "Not directly settable live: the book decides fills"),
            ("merge_gas_usd", "Gas Merge Cost (USD)", "Real cost, not a tuning knob"),
            ("taker_fee_rate", "Taker Fee Rate", "Venue fee coefficient — assumption"),
            ("tick_size", "Tick Size", "Price granularity assumption"),
            ("min_quote_shares", "Min Quote Shares", "Minimum order size floor"),
        ],
        "window_policy": [
            ("entry_timeout_pct", "Entry Timeout (% of window)", "Engine policy — mirrors live config, tuned in research"),
            ("max_start_elapsed_pct", "Max Start Elapsed (% of window)", "Late-start guard — policy, mirrors live"),
            ("reentry_drift_band", "Drift Re-Entry Band", "Re-entry discipline — policy knob"),
            ("min_requote_remaining_sec", "Min Window Left for Re-Entry (s)", "Re-entry time gate — policy"),
            ("reentry_min_remaining_pct", "Re-Entry Min Remaining (% of window)", "Fractional re-entry gate — policy"),
            ("max_reentries_per_window", "Max Re-Entries per Window", "Recovery cap — policy"),
        ],
    }

    def grouped_params(self) -> dict[str, dict[str, Any]]:
        """Return params grouped by control category for UI/API rendering.

        Trading knobs are operator-controlled (live-replicable). Execution
        assumptions are model-side — not directly settable on a live order.
        Window policy mirrors live config but is tuned in research, not at the
        book. Field names and the flat hash contract are untouched.
        """
        flat = asdict(self)
        out: dict[str, dict[str, Any]] = {}
        for group_name, fields in self._PARAM_GROUPS.items():
            grp: dict[str, Any] = {}
            for fname, _label, _why in fields:
                if fname in flat:
                    grp[fname] = flat[fname]
            out[group_name] = grp
        # exit_thresh_by_slug is a dict; flatten into per-key entries for the UI
        if "exit_thresh_by_slug" in flat:
            et = flat["exit_thresh_by_slug"]
            out["trading_knobs"]["exit_default_5m"] = et.get("default_5m", 0.05)
            out["trading_knobs"]["exit_default_15m"] = et.get("default_15m", 0.05)
            out["trading_knobs"]["exit_btc_5m"] = et.get("btc-up-or-down-5m", 0.05)
            out["trading_knobs"]["exit_btc_15m"] = et.get("btc-up-or-down-15m", 0.05)
            out["trading_knobs"]["exit_sol_5m"] = et.get("sol-up-or-down-5m", 0.05)
            out["trading_knobs"]["exit_sol_15m"] = et.get("sol-up-or-down-15m", 0.05)
            # keep the raw dict out of the grouped view but preserve it in the
            # trading_knobs section for consumers that want the whole map
            out["trading_knobs"]["_exit_thresh_by_slug"] = et
        return out

    def __post_init__(self):
        """Validate parameter ranges and finite boundaries."""
        if self.entry_timeout_pct is not None:
            if math.isnan(self.entry_timeout_pct) or not (0.0 <= self.entry_timeout_pct <= 1.0):
                raise ValueError(f"entry_timeout_pct must be between 0.0 and 1.0, got {self.entry_timeout_pct}")
        if self.max_start_elapsed_pct is not None:
            if math.isnan(self.max_start_elapsed_pct) or not (0.0 <= self.max_start_elapsed_pct <= 1.0):
                raise ValueError(
                    f"max_start_elapsed_pct must be between 0.0 and 1.0, got {self.max_start_elapsed_pct}"
                )
        if self.reentry_drift_band is not None:
            if not math.isfinite(self.reentry_drift_band) or not (0.0 <= self.reentry_drift_band <= 0.5):
                raise ValueError(
                    f"reentry_drift_band must be between 0.0 and 0.5, got {self.reentry_drift_band}"
                )
        if self.min_requote_remaining_sec is not None:
            if not math.isfinite(self.min_requote_remaining_sec) or self.min_requote_remaining_sec < 0:
                raise ValueError(
                    f"min_requote_remaining_sec must be >= 0, got {self.min_requote_remaining_sec}"
                )
        if self.reentry_min_remaining_pct is not None:
            if not math.isfinite(self.reentry_min_remaining_pct) or not (0.0 <= self.reentry_min_remaining_pct <= 1.0):
                raise ValueError(
                    f"reentry_min_remaining_pct must be between 0.0 and 1.0, got {self.reentry_min_remaining_pct}"
                )
        if self.max_reentries_per_window is not None:
            if self.max_reentries_per_window < 0:
                raise ValueError(
                    f"max_reentries_per_window must be >= 0, got {self.max_reentries_per_window}"
                )
        if self.entry_delay_sec is not None:
            if not math.isfinite(self.entry_delay_sec) or not (0.0 <= self.entry_delay_sec <= 3600.0):
                raise ValueError(
                    f"entry_delay_sec must be between 0.0 and 3600.0, got {self.entry_delay_sec}"
                )
        if self.entry_band is not None:
            if not math.isfinite(self.entry_band) or not (0.0 <= self.entry_band <= 0.50):
                raise ValueError(
                    f"entry_band must be between 0.0 and 0.50, got {self.entry_band}"
                )

    def exit_thresh(self, slug: str, duration: int, series: str = "") -> float:
        """Return the exit threshold for a given market slug, series, and window duration."""
        if series and series in self.exit_thresh_by_slug:
            return float(self.exit_thresh_by_slug[series])
        if slug in self.exit_thresh_by_slug:
            return float(self.exit_thresh_by_slug[slug])
        for k, v in self.exit_thresh_by_slug.items():
            if k.startswith("default_"):
                continue
            if (series and k in series) or (slug and k in slug):
                return float(v)
        key = f"default_{'5m' if duration == 300 else '15m'}"
        return float(self.exit_thresh_by_slug.get(key, 0.05))

    def params_hash(self) -> str:
        """Stable hash for cache keying slider sweeps (Plan D8)."""
        payload = json.dumps(asdict(self), sort_keys=True, default=str)
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


# --- simulation -------------------------------------------------------------

@dataclass
class WindowResult:
    """Outcome and P&L metrics for a single simulated window."""
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
    start_delay_sec: float = 0.0
    is_partial: bool = False
    err: str = ""
    # Drift-skip re-entry (issue #95): how many times this window was recovered
    # by the re-entry rule after the adverse-open gate skipped it. 0 = the gate
    # never fired or the window stayed skipped. Mirrors live `reentry_count`.
    reentry_count: int = 0
    entry_price_up: float | None = None
    entry_price_down: float | None = None
    exit_price: float | None = None
    settlement_mid: float | None = None


def _mid(book: dict):
    """Compute midpoint price from orderbook best_bid / best_ask."""
    bb, ba = book.get("best_bid"), book.get("best_ask")
    if bb is not None and ba is not None:
        return (bb + ba) / 2.0
    if bb is not None:
        return bb + 0.005
    if ba is not None:
        return ba - 0.005
    return None


def _two_sided_mid(up_book: dict, down_book: dict):
    """Synthetic mid across both legs, or None when either leg is one-sided.

    Mirrors the live engine's `mstate.mid` so the adverse-open gate agrees
    between backtest and live (issue #92). Returning None for a one-sided book
    keeps a thin open from being read as a real skew.
    """
    ubb, uba = up_book.get("best_bid"), up_book.get("best_ask")
    dbb, dba = down_book.get("best_bid"), down_book.get("best_ask")
    if ubb is None or uba is None or dbb is None or dba is None:
        return None
    up_mid = (ubb + uba) / 2.0
    down_mid = (dbb + dba) / 2.0
    return round((up_mid + (1.0 - down_mid)) / 2.0, 4)


def _taker_fee(p: float, rate: float) -> float:
    """Calculate Polymarket crypto taker fee for trade price p."""
    if p is None or p <= 0 or p >= 1:
        return 0.0
    return rate * p * (1.0 - p)


def _classify(mids: list[float]) -> str:
    """Classify window price path into one of: 'no_data', 'flat', 'monotonic', or 'oscillating'.

    - 'no_data': empty mids list.
    - 'oscillating': both max_up and max_down >= 0.02 vs 0.50 base.
    - 'monotonic': either max_up or max_down >= 0.02 (at least one side moves >= 0.02).
    - 'flat': neither direction moves >= 0.02.
    """
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
    """Replay one condition window of ticks under BacktestParams and return WindowResult."""
    if not window_snaps:
        return WindowResult("", "", "", 0, 0, "no_data", 0.0, 0.0,
                            False, False, False, False, "", 0.0, 0.0,
                            0.0, False, "empty")

    first = window_snaps[0]
    cid = first.get("cid", "")
    series = first.get("series", "")
    slug = first.get("slug", "")
    duration = int(first.get("duration", 0))

    first_ts = float(first.get("ts", 0.0) or 0.0)
    start_ts = float(first.get("start_ts", 0.0) or 0.0)
    raw_start_delay_sec = max(0.0, first_ts - start_ts) if (first_ts and start_ts) else 0.0
    start_delay_sec = round(raw_start_delay_sec, 2)
    is_partial = bool(raw_start_delay_sec > 5.0)

    filled_up = False
    filled_down = False
    entry_price_up = None
    entry_price_down = None
    exit_price = None
    settlement_mid = None
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

    exit_thr = params.exit_thresh(slug, duration, series=series)

    # Patient undecided-band maker knobs (issue #145, mirrors live issue #137).
    # `delay_sec` holds all quoting until that far into the window (0 = off);
    # `band` admits only undecided markets at entry time (0 = off). Resting
    # quotes anchor at the first mid AT/AFTER delay expiry (sim2 parity); with
    # delay 0 that is the first valid snapshot, exactly as before.
    delay_sec = params.entry_delay_sec or 0.0
    band = params.entry_band or 0.0
    resting_up: float | None = None
    resting_down: float | None = None
    band_gate_evaluated = False

    entry_cancelled = False
    adverse_gate_evaluated = False
    # Drift-skip re-entry (issue #95): only the adverse-open gate sets this, so a
    # timeout or late-start cancel is never resurrected. Mirrors the live
    # engine's `mstate.adverse_open` distinction.
    adverse_skipped = False
    reentry_count = 0
    # Late start (issue #96): the replay's first snapshot for this window already
    # lands past max_start_elapsed_pct, so the window's open was never observed.
    # Skip it entirely -- no entry, and no adverse-open snapshot from a mid-window
    # mid -- matching LiveTraderEngine's late_start_skip.
    late_start = bool(
        params.max_start_elapsed_pct
        and params.max_start_elapsed_pct > 0
        and duration > 0
        and raw_start_delay_sec >= params.max_start_elapsed_pct * duration
    )
    if late_start:
        entry_cancelled = True
    if params.entry_timeout_pct > 0 and duration > 0:
        cutoff_sec = params.entry_timeout_pct * duration
        if raw_start_delay_sec >= cutoff_sec:
            entry_cancelled = True

    for s_idx, s in enumerate(window_snaps):
        cur_ts = float(s.get("ts", 0.0) or 0.0)
        if cur_ts > 0.0 and start_ts > 0.0:
            elapsed = max(0.0, cur_ts - start_ts)
        else:
            elapsed = float(s_idx)

        # Check entry timeout (Issue #48): if elapsed strictly exceeds cutoff, timeout already passed
        if params.entry_timeout_pct > 0 and duration > 0 and not entry_cancelled:
            if elapsed > (params.entry_timeout_pct * duration):
                if not filled_up and not filled_down:
                    entry_cancelled = True

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
        # exit_thr on the adverse side -- the current drift is no longer
        # monotonic, so don't exit. E.g. if max_down >= exit_thr and then mid
        # is now back within exit_reversal of 0.50, the down excursion was a
        # round-trip and the adverse drift is no longer sustained.
        if max_down >= exit_thr and (0.50 - mid) < params.exit_reversal:
            reversal_seen_down = True
        if max_up >= exit_thr and (mid - 0.50) < params.exit_reversal:
            reversal_seen_up = True

        # Adverse-open gate (issue #92): evaluated once per window against the
        # first snapshot quoting two sides on both legs, so backtest and live
        # agree on which windows are entered. `adverse_skipped` records that the
        # cancel reason was the drift gate (issue #95), so it is the only cancel
        # the re-entry rule below may undo.
        if not adverse_gate_evaluated and not late_start:
            open_mid = _two_sided_mid(ub, db)
            if open_mid is not None:
                adverse_gate_evaluated = True
                if abs(open_mid - 0.50) >= exit_thr:
                    entry_cancelled = True
                    adverse_skipped = True

        # Post-delay entry band (issue #145, mirrors live issue #137): once the
        # entry delay has expired, the first tick with a two-sided book checks
        # |mid - 0.50| against `entry_band` (0 = off). A failure latches the
        # window cancelled; the re-entry rule below only undoes adverse-gate
        # skips, so a band skip is final, matching live. While the band is
        # armed but unevaluated, placement is held too (`band_hold`).
        delay_expired = delay_sec <= 0 or elapsed >= delay_sec
        if resting_up is None and delay_expired:
            anchor = s.get("mid")
            if anchor is None:
                anchor = mid
            anchor = float(anchor)
            resting_up = round(min(0.99, max(0.01, anchor - params.offset)), 3)
            resting_down = round(min(0.99, max(0.01, (1.0 - anchor) - params.offset)), 3)
        if band > 0 and delay_expired and not band_gate_evaluated:
            band_mid = _two_sided_mid(ub, db)
            if band_mid is not None:
                band_gate_evaluated = True
                if abs(band_mid - 0.50) > band:
                    entry_cancelled = True
        band_hold = band > 0 and delay_expired and not band_gate_evaluated

        # Drift-skip re-entry (issue #95): a gate-skipped window is re-entered on
        # a later tick once the mid is back within `reentry_drift_band` of 0.50.
        # Mirrors `LiveTraderEngine._maybe_reenter_drift_skipped`: the skip must
        # have been the gate (`adverse_skipped`), the entry-timeout cutoff must
        # not have passed, at least `min_requote_remaining_sec` must remain to
        # pair two legs, and the per-window cap applies.
        if (adverse_skipped and not filled_up and not filled_down
                and reentry_count < params.max_reentries_per_window):
            entry_timeout_cutoff = (
                params.entry_timeout_pct * duration
                if (0.0 < params.entry_timeout_pct < 1.0 and duration > 0)
                else None
            )
            remaining = max(0.0, duration - elapsed) if duration > 0 else 0.0
            min_remaining = params.min_requote_remaining_sec
            if duration > 0 and 0.0 < params.reentry_min_remaining_pct <= 1.0:
                min_remaining = min(min_remaining,
                                    params.reentry_min_remaining_pct * duration)
            # Measured with `_two_sided_mid`, the same metric the gate above used --
            # `mid` here is the up leg alone, and undoing a two-sided skip with a
            # one-sided reading lets a leg-imbalanced book clear the band while the
            # real drift is still past `exit_thresh`. None means one leg is
            # one-sided, which is not evidence the skew has closed.
            reentry_mid = _two_sided_mid(ub, db)
            # `reentry_drift_band == 0` is documented as "disabled"; without the
            # positive-band guard a two-sided mid of exactly 0.50 has drift 0 and
            # would pass the `<= band` test below, mirroring the live engine's guard.
            if (reentry_mid is not None
                    and params.reentry_drift_band > 0
                    and remaining >= min_remaining
                    and (entry_timeout_cutoff is None or elapsed < entry_timeout_cutoff)
                    and abs(reentry_mid - 0.50) <= min(params.reentry_drift_band, exit_thr)
                    and abs(reentry_mid - 0.50) < exit_thr):
                entry_cancelled = False
                adverse_skipped = False
                reentry_count += 1
                # Re-entry bypasses the entry band entirely (mirrors live,
                # which marks the band evaluated when re-entry is granted).
                band_gate_evaluated = True
                if not filled_up or not filled_down:
                    r_mid = s.get("mid")
                    if r_mid is None:
                        r_mid = reentry_mid
                    r_mid = float(r_mid)
                    if not filled_up:
                        resting_up = round(min(0.99, max(0.01, r_mid - params.offset)), 3)
                    if not filled_down:
                        resting_down = round(min(0.99, max(0.01, (1.0 - r_mid) - params.offset)), 3)

        # Queue gate (0 disables per Plan §2; max_rest_queue_ahead=0 means "always pass")
        if params.queue_gate <= 0:
            queue_ok = True
        elif resting_up is None or resting_down is None:
            queue_ok = False  # delay not expired yet: nothing quotable
        else:
            q_up = sum(sz for p, sz in (ub.get("bids") or {}).items()
                       if float(p) >= resting_up)
            q_dn = sum(sz for p, sz in (db.get("bids") or {}).items()
                       if float(p) >= resting_down)
            queue_ok = (q_up <= params.queue_gate) and (q_dn <= params.queue_gate)

        # Touch pair gate (0 or <= 0 disables per Maker strategy)
        up_ask = ub.get("best_ask")
        dn_ask = db.get("best_ask")
        if params.pair_cost_gate <= 0:
            pair_cost_ok = True
        else:
            touch = None
            if up_ask is not None and dn_ask is not None:
                touch = up_ask + dn_ask
            pair_cost_ok = (touch is None) or (touch <= params.pair_cost_gate)

        if not queue_ok or not pair_cost_ok:
            # An already-filled position must still be eligible to exit even
            # if the live book no longer meets the entry gate. Check exit
            # BEFORE updating the reversal flag, otherwise the crossing tick
            # sets the flag and the exit is suppressed.
            if (filled_up and not filled_down and max_down >= exit_thr
                    and not reversal_seen_down and not exit_taken):
                bb_up = ub.get("best_bid")
                if bb_up is not None:
                    exit_taken = True
                    exit_side = "up"
                    exit_price = bb_up
                    pnl_cents += (bb_up - resting_up) * 100.0
                    fees_cents += _taker_fee(bb_up, params.taker_fee_rate) * 100.0
                    break
            if (filled_down and not filled_up and max_up >= exit_thr
                    and not reversal_seen_up and not exit_taken):
                bb_dn = db.get("best_bid")
                if bb_dn is not None:
                    exit_taken = True
                    exit_side = "down"
                    exit_price = bb_dn
                    pnl_cents += (bb_dn - resting_down) * 100.0
                    fees_cents += _taker_fee(bb_dn, params.taker_fee_rate) * 100.0
                    break
            if not filled_up and not filled_down:
                continue

        # --- FILL DETECTION (Plan fill_model: tape conservative default, cross for strict through-price fills) ---
        # Tape-confirmed: a real trade printed at our resting price (or strictly through for "cross").
        # Book-only: best_ask <= resting_price means book crossed us.
        # Hoist token lookups and guard empty identifiers (prevents "" == "" match).
        up_token = (first.get("up_token") or (ub.get("token_id") or "")).strip()
        dn_token = (first.get("down_token") or (db.get("token_id") or "")).strip()
        quotable = (resting_up is not None and resting_down is not None
                    and not band_hold)
        can_fill_up = (not filled_up) and (not entry_cancelled or filled_down) and quotable
        can_fill_down = (not filled_down) and (not entry_cancelled or filled_up) and quotable
        for trade in s.get("tape_delta") or []:
            tasset = str(trade.get("asset", "")).strip()
            if not tasset:
                continue
            tprice = float(trade.get("price", 0))
            if up_token and tasset == up_token and can_fill_up:
                if params.fill_model in ("tape", "both") and abs(tprice - resting_up) <= (params.tick_size + 1e-6):
                    filled_up = True
                    can_fill_up = False
                elif params.fill_model == "cross" and tprice <= (resting_up - params.tick_size + 1e-6):
                    filled_up = True
                    can_fill_up = False
            if dn_token and tasset == dn_token and can_fill_down:
                if params.fill_model in ("tape", "both") and abs(tprice - resting_down) <= (params.tick_size + 1e-6):
                    filled_down = True
                    can_fill_down = False
                elif params.fill_model == "cross" and tprice <= (resting_down - params.tick_size + 1e-6):
                    filled_down = True
                    can_fill_down = False
        if params.fill_model in ("book", "both"):
            if can_fill_up and up_ask is not None and up_ask <= resting_up:
                filled_up = True
                can_fill_up = False
            if can_fill_down and dn_ask is not None and dn_ask <= resting_down:
                filled_down = True
                can_fill_down = False
        elif params.fill_model == "cross":
            if can_fill_up and up_ask is not None and up_ask <= (resting_up - params.tick_size + 1e-6):
                filled_up = True
                can_fill_up = False
            if can_fill_down and dn_ask is not None and dn_ask <= (resting_down - params.tick_size + 1e-6):
                filled_down = True
                can_fill_down = False

        # --- PAIR COMPLETION ---
        if filled_up and filled_down and not pair_captured and not exit_taken:
            pair_captured = True
            pnl_cents += (1.00 - (resting_up + resting_down)) * 100.0
            # merge_gas_usd is a per-transaction cost; amortize over the
            # actual shares in the pair so pnl_cents stays per-share.
            pnl_cents -= (params.merge_gas_usd * 100.0) / max(1, params.quote_shares)
            break

        # --- EXIT (one side filled, mid drifted past thresh without reversal) ---
        # Check BEFORE we update the reversal flag this tick so the crossing
        # tick is the exit tick (otherwise the flag toggles the same tick and
        # the exit is suppressed).
        if (filled_up and not filled_down and max_down >= exit_thr
                and not reversal_seen_down and not exit_taken):
            bb_up = ub.get("best_bid")
            if bb_up is not None:
                exit_taken = True
                exit_side = "up"
                exit_price = bb_up
                pnl_cents += (bb_up - resting_up) * 100.0
                fees_cents += _taker_fee(bb_up, params.taker_fee_rate) * 100.0
                break
        if (filled_down and not filled_up and max_up >= exit_thr
                and not reversal_seen_up and not exit_taken):
            bb_dn = db.get("best_bid")
            if bb_dn is not None:
                exit_taken = True
                exit_side = "down"
                exit_price = bb_dn
                pnl_cents += (bb_dn - resting_down) * 100.0
                fees_cents += _taker_fee(bb_dn, params.taker_fee_rate) * 100.0
                break

        # Check entry timeout (Issue #48): cancel unfilled entry quotes if 0 legs filled
        # Evaluated after snapshot fills so trades in the cutoff snapshot are not dropped
        if params.entry_timeout_pct > 0 and duration > 0 and not entry_cancelled:
            if elapsed >= (params.entry_timeout_pct * duration):
                if not filled_up and not filled_down:
                    entry_cancelled = True

    if filled_up:
        entry_price_up = resting_up
    if filled_down:
        entry_price_down = resting_down

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
                exit_price = lb
                settlement_mid = lb
                pnl_cents += (lb - resting_up) * 100.0
                fees_cents += _taker_fee(lb, params.taker_fee_rate) * 100.0
            elif filled_down and not filled_up and db_bid is not None:
                exit_price = db_bid
                settlement_mid = db_bid
                pnl_cents += (db_bid - resting_down) * 100.0
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
        start_delay_sec=start_delay_sec,
        is_partial=is_partial,
        err=err,
        reentry_count=reentry_count,
        entry_price_up=entry_price_up,
        entry_price_down=entry_price_down,
        exit_price=exit_price,
        settlement_mid=settlement_mid,
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
      Both `per_series` and `overall` levels carry `reentry_count` /
      `reentry_pnl_cents` (issue #95): how many windows were recovered via
      drift-skip re-entry and their net P&L (per share, unscaled by size).
    """
    snaps_list = list(snaps)
    n_snaps = len(snaps_list)
    per_window: list[WindowResult] = []
    for _cid, group in group_by_cid(snaps_list):
        if not group:
            continue
        if params.max_start_delay_sec > 0:
            first_ts = float(group[0].get("ts", 0.0) or 0.0)
            start_ts = float(group[0].get("start_ts", 0.0) or 0.0)
            delay = max(0.0, first_ts - start_ts) if (first_ts and start_ts) else 0.0
            if delay > params.max_start_delay_sec:
                continue
        per_window.append(_simulate_window(group, params))

    per_series: dict[str, dict] = defaultdict(lambda: {
        "windows": 0, "pair": 0, "exit": 0, "filled_up_only": 0,
        "filled_down_only": 0, "oscillating": 0, "monotonic": 0, "flat": 0,
        "total_pnl_cents": 0.0, "total_fees_cents": 0.0,
        "wins": 0, "peak_pnl": 0.0, "cum_pnl": 0.0, "max_dd": 0.0,
        "reentry_count": 0, "reentry_pnl_cents": 0.0,
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

        exit_info = f"exit_{w.exit_side}" if w.exit_taken else ("pair_merged" if w.pair_captured else "-")
        trades_sample.append({
            "slug": w.slug,
            "series": w.series,
            "both_filled": w.pair_captured,
            "exit_triggered": w.exit_taken,
            "up_filled": w.filled_up,
            "down_filled": w.filled_down,
            "entry_up": w.entry_price_up,
            "entry_down": w.entry_price_down,
            "exit_price": w.exit_price,
            "exit_side": w.exit_side,
            "settlement_mid": w.settlement_mid,
            "pnl_cents": round(w.pnl_cents, 2),
            "exit_reason": exit_info,
            "start_delay_sec": w.start_delay_sec,
            "is_partial": w.is_partial,
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
        if w.reentry_count > 0:
            a["reentry_count"] += 1
            a["reentry_pnl_cents"] += w.pnl_cents - w.fees_cents

        if w.pnl_cents > 0:
            a["wins"] += 1
        a["cum_pnl"] += w.pnl_cents
        if a["cum_pnl"] > a["peak_pnl"]:
            a["peak_pnl"] = a["cum_pnl"]
        s_dd = a["peak_pnl"] - a["cum_pnl"]
        if s_dd > a["max_dd"]:
            a["max_dd"] = s_dd

    def _finalize(d: dict) -> dict:
        """Compute aggregate summary ratios and rates from raw metric counts."""
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
            "reentry_count": d.get("reentry_count", 0),
            "reentry_pnl_cents": round(d.get("reentry_pnl_cents", 0.0), 4),
        }

    overall = {
        "windows": sum(s["windows"] for s in per_series.values()),
        "pair": sum(s["pair"] for s in per_series.values()),
        "exit": sum(s["exit"] for s in per_series.values()),
        "total_pnl_cents": sum(s["total_pnl_cents"] for s in per_series.values()),
        "total_fees_cents": sum(s["total_fees_cents"] for s in per_series.values()),
        "wins": wins_count,
        "max_dd": max_dd,
        "reentry_count": sum(s["reentry_count"] for s in per_series.values()),
        "reentry_pnl_cents": sum(s["reentry_pnl_cents"] for s in per_series.values()),
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
