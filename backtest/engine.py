"""SPREAD-2 backtest engine.

Pure function `replay(snaps, params) -> results` that consumes a chronological
list of tick dicts (the same shape written by `scripts/collect_ticks.py`) and
returns per-window P&L plus aggregate stats. Zero network calls.

Per Plan §2 + D3 (dir/range input, group by cid across files) + D6 (engine
importable, CLI thin wrapper) + D8 (cid index for fast slider sweep):

  snaps = load_ticks("run/ticks/")  # iterates all *.jsonl[.gz]
  for cid, window_snaps in group_by_cid(snaps):  # handles midnight split
      ...
  params = BacktestParams(offset=0.02, queue_gate=50, exit_thresh_by_slug={...})
  results = replay(snaps, params)

There is one fill rule and it is not configurable (issue #226, ADR-0002):
`book_math.resting_bid_filled` -- a print at our price, or an ask fully through
it. Both detect the same event, our resting limit order being taken, so the
fill price is always our own and an entry never pays a fee.
"""
from __future__ import annotations
import gzip
import copy
import hashlib
from functools import lru_cache
import json
import math
from collections import defaultdict
from dataclasses import dataclass, field, asdict

from strategy import book_math
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
    # Issue #227: the ceiling on a *completed pair*, and it caps the leg chase
    # only. A binary pair settles at 1.00, so a cap above that authorises a
    # guaranteed loss; both engines refuse one. Named for what it is, matching
    # live. It was `pair_cost_gate` at 1.05 -- a research value chosen to sit
    # above the top of live's range purely so a sweep could switch off an
    # entry-side block that no longer exists.
    max_pair_cost: float = 0.99
    exit_thresh_by_slug: dict = field(default_factory=lambda: {
        "btc-up-or-down-5m": 0.05, "sol-up-or-down-5m": 0.05,
        "btc-up-or-down-15m": 0.05, "sol-up-or-down-15m": 0.05,
        "default_5m": 0.05, "default_15m": 0.05,
    })
    exit_reversal: float = 0.02
    quote_shares: int = 5
    tick_size: float = 0.001
    merge_gas_usd: float = 0.0
    taker_fee_rate: float = 0.07     # crypto fee coefficient
    min_quote_shares: int = 5
    # Patient entry delay (issue #145, mirrors live issue #137): `entry_delay`
    # holds all quoting until that far into the window (0 = off).
    entry_delay_sec: float = 0.0
    # Issue #228: the one quotable range, replacing `entry_band` and the
    # adverse-open gate (`docs/engine-decision-rules.md` §6). A structural
    # limit, not a tuning knob (ADR-0003): inside it the window is quoted,
    # outside it placement holds for that tick only — evaluated every tick on
    # the two-sided mid, never latched. Inclusive on both ends.
    quote_range: tuple[float, float] = (0.10, 0.90)
    # Issue #229: Dead zone governs the end of the window (rules §8 & §14).
    # Structural limit, not a tuning knob: in the dead zone, open nothing, and
    # close what is open. Replaces entry_timeout_pct, naked_leg_timeout_pct,
    # max_start_elapsed_pct, and max_start_delay_sec.
    dead_zone_unit: str = "pct"        # "pct" (fraction of window) or "sec" (absolute seconds)
    dead_zone_val: float = 0.10        # default 10% (0.10) of window remaining; 0.0 disables
    # Issue #229 / rule §14: what happens to an unpaired leg in the dead zone.
    # Replaces stop_loss_enabled. "close" (default) exits at the book; "hold" carries
    # the leg to settlement (pays 1.00 or 0.00).
    naked_leg_at_expiry: str = "close" # "close" | "hold"
    # Issue #164: mirrors LiveTraderEngine.enable_leg_chase (issue #123). Once
    # one leg fills, the other is re-anchored each tick toward its ask, capped
    # so the pair still costs at most `max_pair_cost` — converting a naked leg
    # into a pair at or under the cap instead of riding it. The quote is only
    # ever raised, never lowered. False = off = today's behaviour.
    enable_leg_chase: bool = False

    # ── param grouping metadata ──────────────────────────────────────────────
    # Separates operator-controlled (live-replicable) knobs from execution
    # assumptions and internal window policy so the UI and API can render them
    # in distinct sections without touching any field names or the hash contract.
    # Each group lists (field_name, label, why, unit, bounds, surfaces,
    # param_class). `param_class` (issue #233) is one of "tuning", "structural",
    # or "assumption": a tuning knob is swept in daily operation, a structural
    # limit bounds what the engine may do at all (ADR-0003), an execution
    # assumption is model-side cost. Query via `tuning_knobs()` /
    # `structural_limits()` / `execution_assumptions()` / `param_class_for()`.
    # `bounds` is the (low, high) range the UI renders and the API clamps to —
    # not a claim about `__post_init__`, which validates only a subset (see
    # `param_spec`). `surfaces` names which tabs may render the knob.
    # Unknown keys silently drop so new fields don't break the grouping on a
    # missing-entry error.
    #
    # Issue #164: this is the single source of truth. Backtest and Cockpit
    # drifted — different labels for one knob, and each missing knobs the other
    # had — because both hand-rolled their own copies. A label, unit, default or
    # bound written anywhere else in the dashboard is a defect.
    _PARAM_GROUPS: ClassVar[dict[str, list[tuple]]] = {
        "trading_knobs": [
            ("offset", "Spread Offset ($)", "You set this live on the book",
             "$", (0.001, 0.49), ("backtest", "cockpit"), "tuning"),
            ("queue_gate", "Queue Depth Filter (shares)", "You choose how many orders ahead to clear through",
             "shares", (0.0, 100000.0), ("backtest",), "tuning"),
            # A structural limit, not a tuning knob (ADR-0003): it bounds what
            # the chase may do at all rather than tuning how it performs. One
            # range on both surfaces — issue #227 deleted the entry-side block
            # whose disabling was the only reason the Backtest wanted 2.0.
            ("max_pair_cost", "Max Pair Cost ($)", "The most the chase may pay to complete a pair",
             "$", (0.50, 1.00), ("backtest", "cockpit"), "structural"),
            ("quote_shares", "Share Size per Leg", "Your sizing decision",
             "shares", (5, 10000), ("backtest", "cockpit"), "tuning"),
            ("entry_delay_sec", "Entry Delay (s)", "You hold quotes until the window matures",
             "s", (0.0, 3600.0), ("backtest", "cockpit"), "tuning"),
            # Issue #228: structural limit (ADR-0003) replacing the band and
            # the adverse-open gate. Bounds are the price domain itself; the
            # dashboard renders two inputs (lo/hi), not one knob.
            ("quote_range", "Quotable Range (mid lo/hi)", "You quote only while the two-sided mid is inside this range",
             "$", (0.0, 1.0), ("backtest", "cockpit"), "structural"),
            ("exit_thresh_by_slug", "Exit Stop Loss ($)", "Your stop placement — per series / duration",
             "$", None, ("backtest", "cockpit"), "tuning"),
            # Issue #229 / rule §14: what happens to an unpaired leg in the dead zone.
            ("naked_leg_at_expiry", "Naked Leg at Expiry", "close at book (default) or hold to settlement",
             "str", None, ("backtest", "cockpit"), "structural"),
            ("enable_leg_chase", "Leg Chase Enabled", "After one leg fills, re-anchor the other toward its ask within the pair-cost cap",
             "bool", None, ("backtest", "cockpit"), "tuning"),
            ("exit_reversal", "Reversal Buffer ($)", "How far back toward 0.50 cancels a stop you were about to take",
             "$", (0.001, 0.50), ("backtest", "cockpit"), "tuning"),
        ],
        "execution_assumptions": [
            ("merge_gas_usd", "Gas Merge Cost ($)", "Real cost, not a tuning knob",
             "$", (0.0, 100.0), ("backtest",), "assumption"),
            ("taker_fee_rate", "Taker Fee Rate", "Venue fee coefficient — assumption",
             "coef", (0.0, 1.0), ("backtest",), "assumption"),
            ("tick_size", "Tick Size ($)", "Price granularity assumption",
             "$", (0.0, 1.0), ("backtest",), "assumption"),
            ("min_quote_shares", "Min Quote Shares", "Minimum order size floor",
             "shares", (1, 100000), ("backtest",), "assumption"),
        ],
        "window_policy": [
            # Issue #229: Dead zone governs the end of the window (rules §8 & §14).
            # Structural limits (ADR-0003), not tuning knobs.
            ("dead_zone_val", "Dead Zone Threshold", "Tail of window that is untradeable (0.10 default; 0 disables)",
             "% or s", (0.0, 3600.0), ("backtest", "cockpit"), "structural"),
            ("dead_zone_unit", "Dead Zone Unit", "Whether threshold is % of window or absolute seconds",
             "str", None, ("backtest", "cockpit"), "structural"),
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
        for group_name, entries in self._PARAM_GROUPS.items():
            grp: dict[str, Any] = {}
            for entry in entries:
                fname = entry[0]
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

    @classmethod
    def param_spec(cls) -> dict[str, dict[str, dict[str, Any]]]:
        """The full definition of every knob, grouped, for UIs and validators.

        Issue #164: Backtest and Cockpit each hand-rolled labels, units,
        defaults and bounds, which is how they came to disagree about the same
        knob and to each miss knobs the other had. Both now render and validate
        from this, so a new field reaches every surface it declares and a
        label exists in exactly one place.

        `bounds` is the `(low, high)` range the UI renders and the API clamps
        to, or None for a non-numeric field. It is NOT a claim about
        `__post_init__`: that validates a subset (the fractions and the two
        patient-maker knobs) and leaves the rest unchecked, so a caller that
        builds `BacktestParams` directly — every driver in `research/sweeps/`
        does — can still construct a value outside these bounds. The registry
        constrains what a *request* may ask for, not what the dataclass will
        accept. `surfaces` says which tabs may show a knob: an execution
        assumption like `taker_fee_rate` is not something an operator sets on a
        live order, so it is backtest-only by design.
        """
        return copy.deepcopy(cls._param_spec_cached())

    @classmethod
    @lru_cache(maxsize=1)
    def _param_spec_cached(cls) -> dict[str, dict[str, dict[str, Any]]]:
        """Build the spec once. Never hand this object out directly.

        `param_spec()` returns a deep copy: the cached dict is shared by every
        caller, and one of them mutating a label or a bounds tuple in place
        would silently rewrite what every other surface renders — including
        `/api/params/spec`, which is the definition both tabs read.
        """
        defaults = asdict(cls())
        out: dict[str, dict[str, dict[str, Any]]] = {}
        for group_name, entries in cls._PARAM_GROUPS.items():
            grp: dict[str, dict[str, Any]] = {}
            for entry in entries:
                fname, label, why, unit, bounds, surfaces, param_class = entry
                if fname not in defaults:
                    continue
                grp[fname] = {
                    "label": label, "why": why, "unit": unit,
                    "default": defaults[fname], "bounds": bounds,
                    "surfaces": tuple(surfaces),
                    "param_class": param_class,
                }
            out[group_name] = grp
        return out

    @classmethod
    def bounds_for(cls, name: str, surface: str = "") -> "tuple[float, float] | None":
        """Bounds for one knob.

        Issue #233: the optional per-surface override (7th tuple element) was
        replaced by `param_class`; no knob declared an override anyway, so the
        mechanism is gone rather than kept unused.
        """
        spec = cls.spec_for(name)
        return spec["bounds"]

    @classmethod
    def param_class_for(cls, name: str) -> str:
        """The class of one knob: "tuning", "structural", or "assumption" (issue #233)."""
        return cls.spec_for(name)["param_class"]

    @classmethod
    def _names_in_class(cls, klass: str) -> set[str]:
        """Field names whose registry entry declares `param_class` == `klass`.

        Raises rather than silently skipping a malformed entry: a tuple missing
        `param_class` is a defect to surface, not one to hide (#233).
        """
        out: set[str] = set()
        for entries in cls._PARAM_GROUPS.values():
            for entry in entries:
                if len(entry) != 7:
                    raise ValueError(
                        f"_PARAM_GROUPS entry {entry[0]!r} has {len(entry)} "
                        "elements, need 7 including param_class")
                if entry[6] == klass:
                    out.add(entry[0])
        return out

    @classmethod
    def structural_limits(cls) -> set[str]:
        """Knobs that bound what the engine may do at all (ADR-0003) — not swept casually."""
        return cls._names_in_class("structural")

    @classmethod
    def tuning_knobs(cls) -> set[str]:
        """Operator-tunable knobs, live-replicable — the default sweep axes."""
        return cls._names_in_class("tuning")

    @classmethod
    def execution_assumptions(cls) -> set[str]:
        """Model-side cost/assumption knobs, not set on live orders."""
        return cls._names_in_class("assumption")

    @classmethod
    def spec_for(cls, name: str) -> dict[str, Any]:
        """One knob's spec, or KeyError naming the field that is unregistered."""
        for grp in cls._param_spec_cached().values():
            if name in grp:
                # One knob, copied — cheap, and still no handle on the cache.
                return copy.deepcopy(grp[name])
        raise KeyError(f"{name!r} is not in BacktestParams._PARAM_GROUPS")

    def __post_init__(self):
        """Validate parameter ranges and finite boundaries."""
        # Issue #229: Dead zone (rules §8 & §14)
        if self.dead_zone_unit not in ("pct", "sec"):
            raise ValueError(f"dead_zone_unit must be 'pct' or 'sec', got {self.dead_zone_unit!r}")
        if (
            isinstance(self.dead_zone_val, bool)
            or not isinstance(self.dead_zone_val, (int, float))
            or not math.isfinite(self.dead_zone_val)
            or self.dead_zone_val < 0.0
        ):
            raise ValueError(f"dead_zone_val must be a finite number >= 0.0, got {self.dead_zone_val!r}")
        if self.dead_zone_unit == "pct" and self.dead_zone_val > 1.0:
            raise ValueError(f"dead_zone_val for unit 'pct' must be <= 1.0, got {self.dead_zone_val}")
        if self.naked_leg_at_expiry not in ("close", "hold"):
            raise ValueError(f"naked_leg_at_expiry must be 'close' or 'hold', got {self.naked_leg_at_expiry!r}")
        if self.entry_delay_sec is not None:
            if not math.isfinite(self.entry_delay_sec) or not (0.0 <= self.entry_delay_sec <= 3600.0):
                raise ValueError(
                    f"entry_delay_sec must be between 0.0 and 3600.0, got {self.entry_delay_sec}"
                )
        # Issue #227: a structural limit, enforced here and not only at the API
        # clamp. Every driver in `research/sweeps/` builds this dataclass
        # directly, and the one value that matters — above 1.00 — is exactly
        # the one a sweep reached for. There is no "off": 0.0 is out of
        # range, and so is None (the field is a plain float, and an
        # unvalidated None would crash the chase at `min(ask, None)`).
        if (
            isinstance(self.max_pair_cost, bool)
            or not isinstance(self.max_pair_cost, (int, float))
            or not math.isfinite(self.max_pair_cost)
            or not (0.50 <= self.max_pair_cost <= 1.00)
        ):
            raise ValueError(
                f"max_pair_cost must be between 0.50 and 1.00, got {self.max_pair_cost}"
            )
        # Issue #228: a structural limit, enforced here and not only at the API
        # clamp (the #227 pattern). Every driver in `research/sweeps/` builds
        # this dataclass directly. No "off": a (lo, hi) pair with
        # 0.0 <= lo < hi <= 1.0, inclusive on both ends (the range judges the
        # market, and a boundary mid is inside).
        qr = self.quote_range
        if (
            not isinstance(qr, (tuple, list))
            or len(qr) != 2
            or isinstance(qr[0], bool)
            or isinstance(qr[1], bool)
            or not isinstance(qr[0], (int, float))
            or not isinstance(qr[1], (int, float))
            or not math.isfinite(qr[0])
            or not math.isfinite(qr[1])
            or not (0.0 <= qr[0] < qr[1] <= 1.0)
        ):
            raise ValueError(
                f"quote_range must be (lo, hi) with 0.0 <= lo < hi <= 1.0, got {qr!r}"
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
    # Issue #228: the re-entry mechanism is deleted, so no window is ever
    # recovered and this is always 0. The field stays until T5 removes its
    # last readers (dashboard summary, sim2); deleting it now would break
    # every consumer that still aggregates it.
    reentry_count: int = 0
    entry_price_up: float | None = None
    entry_price_down: float | None = None
    exit_price: float | None = None
    settlement_mid: float | None = None
    # Issue #164: which leg the chase re-anchored ("up"/"down"/""), and the
    # price it was last moved to. `sim2` already returned the former; the
    # latter makes the pair-cost cap observable even when the chased leg
    # never filled, which is exactly when a breached cap would go unnoticed.
    chased_leg: str = ""
    chased_resting: float | None = None
    # Issue #191: how a naked leg carried to window close was valued, and
    # whether that value came from anything other than its own final bid.
    # Without this a stale latched mark and a fresh quote are indistinguishable
    # in the results, and an abstention looks identical to a flat window.
    settled_unmarked: bool = False
    settle_source: str = ""
    entered: bool = False


# Issue #170: these used to be local copies that disagreed with the collector
# and the live engine on one-sided and empty books -- the books that decide
# entries. `strategy/book_math` is now the single definition for all consumers.
_mid = book_math.mid
_two_sided_mid = book_math.two_sided_mid


def _taker_fee(p: float, rate: float) -> float:
    """Calculate Polymarket crypto taker fee for trade price p."""
    if p is None or p <= 0 or p >= 1:
        return 0.0
    return rate * p * (1.0 - p)


def _quote(x: object) -> float | None:
    """A price usable as an executable mark, or None.

    Mirrors the validity window `LiveTrader._resolve_exit_bid` enforces
    (issue #160): a quote is only a mark inside `0.0 < p <= 1.0`. A literal
    `0.0` is how a venue spells "no bid", not a bid of zero — Polymarket's
    tick size makes a genuine zero unquotable — so it must fall through to the
    next resolution stage rather than be marked against.

    Deliberately stricter than `_mid`, which folds a `0.0` into the midpoint
    like any other price. The two are asked different questions: this one asks
    "can the leg be sold here", where a sentinel zero is no answer at all, and
    `_mid` asks "which way did the window go", where a quote pinned at zero is
    strong evidence on its own.
    """
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or not (0.0 < v <= 1.0):
        return None
    return v


def _clamp_mark(p: float) -> float:
    """Clamp a synthesized mark away from the unquotable ends, as live does."""
    return round(max(0.0001, min(0.9999, p)), 4)


def resolve_redemption(up_book: dict | None, down_book: dict | None,
                       held_up: bool, resting: float) -> tuple[bool | None, float]:
    """`(held_side_won, delta_cents)` for a naked leg carried to expiry.

    The binary redeems at 1 or 0, so a leg nobody will quote is still worth
    something. Direction comes from the held side's own final mid, falling back
    to the complement of the opposite side's mid when the held book prices
    nothing at all. `(None, 0.0)` when the window names no winner — no mid on
    either book, or a mid of exactly 0.50 — because booking a coin flip would
    replace one wrong number with another.

    Takes the two books rather than four loose prices: every caller already has
    them in this shape, and four same-typed `float | None` positionals would
    let a transposed up/down pair type-check and silently invert the answer.

    The `== 0.5` test is exact on purpose. Live's own stage-5 uses a 1e-4
    tolerance, but this rule is also what `summarize(settle_correct=True)` has
    been applying to every sweep result, and widening the abstention band here
    would move published numbers rather than fix them (issue #191 scope fence).

    This is the single definition of the rule. `research/sweeps/ev_lab.py` and
    `research/sweeps/sim2.py` each carried their own copy; issue #182 was
    caused by exactly that kind of divergence between the audit and the
    simulator, so a second copy is a defect (issue #191).
    """
    held = _mid(up_book if held_up else down_book)
    if held is None:
        opp = _mid(down_book if held_up else up_book)
        held = None if opp is None else (1.0 - opp)
    if held is None or held == 0.5:
        return None, 0.0
    won = held > 0.5
    return won, ((1.0 - resting) if won else -resting) * 100.0


# Every value `resolve_naked_settlement` can report, in resolution order, so a
# result says how it was reached rather than leaving a stale latched mark
# indistinguishable from a fresh quote. The last entry is the abstention.
SETTLE_SOURCES = ("direct_bid", "complement_ask", "latched_bid",
                  "latched_complement_ask", "redeemed", "unresolved")


def resolve_naked_settlement(snaps: list[dict], held_up: bool,
                             resting: float) -> tuple[float | None, float, str]:
    """`(mark, delta_cents, source)` for a naked leg at window close.

    Ports `LiveTrader._resolve_exit_bid` (issue #160) into the replay: the held
    leg's own final bid, then the binary complement of the opposite ask, then
    the last valid quote of each seen anywhere in the window. Live's fifth
    stage — a synthetic mid — is replaced by outright redemption, because a
    replay knows how the window ended and a live engine at rollover does not.

    `mark` is None for `redeemed` and `unresolved`: neither is a closing trade,
    so neither carries a taker fee.

    Before this existed the engine stopped after the first stage and booked
    `0.00c` when it failed. A losing contract loses its bid side before expiry
    while a winning one keeps a bid near 1.00, so the omission ran one way: of
    181 unmarked windows in the 3-day capture, 181 were losers (issue #191).
    """
    if not snaps:
        return None, 0.0, SETTLE_SOURCES[-1]
    held_key = "up_book" if held_up else "down_book"
    opp_key = "down_book" if held_up else "up_book"
    last = snaps[-1]
    last_held = last.get(held_key) or {}
    last_opp = last.get(opp_key) or {}

    bid = _quote(last_held.get("best_bid"))
    if bid is not None:
        return bid, (bid - resting) * 100.0, "direct_bid"

    # The complement is built from the opposite ASK, never the opposite bid:
    # `1 - ask_opp` synthesizes a bid on this leg, which is what closing a long
    # has to cross. `1 - bid_opp` would synthesize an ask (live, issue #160).
    opp_ask = _quote(last_opp.get("best_ask"))
    if opp_ask is not None:
        mark = _clamp_mark(1.0 - opp_ask)
        return mark, (mark - resting) * 100.0, "complement_ask"

    for s in reversed(snaps):
        latched = _quote((s.get(held_key) or {}).get("best_bid"))
        if latched is not None:
            return latched, (latched - resting) * 100.0, "latched_bid"

    for s in reversed(snaps):
        latched_opp = _quote((s.get(opp_key) or {}).get("best_ask"))
        if latched_opp is not None:
            mark = _clamp_mark(1.0 - latched_opp)
            return mark, (mark - resting) * 100.0, "latched_complement_ask"

    won, delta = resolve_redemption(last.get("up_book"), last.get("down_book"),
                                    held_up, resting)
    if won is None:
        return None, 0.0, SETTLE_SOURCES[-1]
    return None, delta, "redeemed"


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


def _window_clock(first: dict) -> tuple[float, float] | None:
    """Return `(start_ts, window_length)` for a window, or None when it has no clock.

    Invariant 1 (issue #224), and the mirror of `LiveTraderEngine._window_clock`:

        window_length = end_ts - start_ts
        elapsed       = snapshot_ts - start_ts

    A usable pair is two finite numbers with `end_ts > start_ts`. The absolute
    epoch position is not checked -- replays and fixtures legitimately use a
    synthetic timebase, and no real fault gets past `end_ts > start_ts` by way of
    one. A window with no usable pair has no clock, so no time gate may be
    evaluated and the window is not traded.
    """
    raw_start = first.get("start_ts")
    raw_end = first.get("end_ts")
    if raw_start is None or raw_end is None:
        return None
    try:
        start_ts = float(raw_start)
        end_ts = float(raw_end)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(start_ts) and math.isfinite(end_ts)):
        return None
    if end_ts <= start_ts:
        return None
    return (start_ts, end_ts - start_ts)


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

    # Invariant 1 (issue #224): one definition of the window clock, taken from the
    # market's own metadata. `duration` below is the collector's *series label*
    # (300 / 900), kept only to key the per-duration stop threshold and to report
    # the window -- no time gate may read it, or there are two clocks again.
    clock = _window_clock(first)
    if clock is None:
        return WindowResult(cid, series, slug, duration, len(window_snaps), "no_clock",
                            0.0, 0.0, False, False, False, False, "", 0.0, 0.0,
                            0.0, False, "no_clock")
    start_ts, window_length = clock
    end_ts = start_ts + window_length

    first_ts = float(first.get("ts", 0.0) or 0.0)
    raw_start_delay_sec = max(0.0, first_ts - start_ts) if first_ts else 0.0
    start_delay_sec = round(raw_start_delay_sec, 2)
    is_partial = bool(raw_start_delay_sec > 5.0)

    filled_up = False
    filled_down = False
    entry_price_up = None
    entry_price_down = None
    exit_price = None
    settlement_mid = None
    settled_unmarked = False
    settle_source = ""
    exit_taken = False
    exit_side = ""
    pair_captured = False
    mids: list[float] = []
    max_up = 0.0
    max_down = 0.0
    # Window range (max_up / max_down, measured from 0.50) classifies the
    # window and feeds the dashboard's oscillation stats. Position risk is a
    # different quantity: the adverse excursion of a filled leg, measured from
    # the price that leg actually entered at (issue #209). Keeping the two
    # apart leaves the historical range metrics untouched while the stop and
    # the reversal latch follow the position.
    adverse_drift_up = 0.0            # mid above a filled DOWN leg's entry
    adverse_drift_down = 0.0          # mid below a filled UP leg's entry
    reversal_seen_up = False          # mid came back toward the entry after excursion
    reversal_seen_down = False
    pnl_cents = 0.0
    fees_cents = 0.0
    err = ""
    window_entered = False

    exit_thr = params.exit_thresh(slug, duration, series=series)
    naked_since_elapsed: float | None = None
    # Which leg (if any) the chase moved, so the entry price recorded for it is
    # the price it actually rested at when it filled, not a later chase step.
    chased_leg = ""

    # Patient entry delay (issue #145, mirrors live issue #137): `entry_delay`
    # holds all quoting until that far into the window (0 = off).
    # Resting quotes anchor at the first mid AT/AFTER delay expiry (sim2
    # research-sim parity: sim2 anchors from its cached window mids, same
    # one-sided source as `s["mid"]` here); with delay 0 that is the first
    # valid snapshot, exactly as before. Explicit None handling matches the
    # validator (`__post_init__` owns range/finiteness for constructed params).
    # Issue #228: the entry band is deleted and the range below is judged on
    # the anchor's own two-sided mid.
    entry_delay = 0.0 if params.entry_delay_sec is None else params.entry_delay_sec
    quote_lo, quote_hi = params.quote_range
    resting_up: float | None = None
    resting_down: float | None = None
    original_resting_up: float | None = None
    original_resting_down: float | None = None
    # Whether a quote has actually been exposed to the book (issue #225). The
    # backtest has no order object, so "an order is live" is "the quote reached
    # fill detection on some earlier tick and has not been cancelled since" --
    # the condition live spells as `order_id_up or order_id_down`. Until then
    # the anchor is repriced every tick, so the price that goes on the book is
    # the mid at placement time and not one carried over from before whatever
    # was holding placement cleared.
    orders_live = False
    entry_cancelled = False
    # Dead zone at window start (issue #229 / rule §8):
    # A window whose first observed tick already lands inside the dead zone is
    # not entered at all -- "in the dead zone, open nothing".
    # Replaces max_start_elapsed_pct and max_start_delay_sec.
    first_remaining = max(0.0, window_length - raw_start_delay_sec)
    if book_math.is_in_dead_zone(first_remaining, window_length, params.dead_zone_val, params.dead_zone_unit):
        entry_cancelled = True

    for s in window_snaps:
        # Invariant 1 (#224). This used to fall back to `float(s_idx)`, counting
        # snapshots as if each were one second -- and the collector misses
        # seconds, the same gap that makes the tape incomplete, so every time
        # gate in such a window was measured against a made-up clock with
        # nothing reporting it. A snapshot with no timestamp carries no clock
        # reading, so it is skipped rather than assigned one.
        raw_ts = s.get("ts")
        if raw_ts is None:
            continue
        try:
            cur_ts = float(raw_ts)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(cur_ts):
            continue
        elapsed = max(0.0, cur_ts - start_ts)
        rem_sec = max(0.0, end_ts - cur_ts)
        in_dead_zone = book_math.is_in_dead_zone(rem_sec, window_length, params.dead_zone_val, params.dead_zone_unit)

        # Check dead zone for unentered / unfilled windows (rule §8):
        # A filled leg is governed by the naked-leg rule below, not by the
        # entry-cancel path — otherwise "close" could never fire because the
        # naked-leg handler requires `not exit_taken`, which entry-cancel
        # pre-empts by breaking the leg out of the fill detector.
        if in_dead_zone and not entry_cancelled:
            if not filled_up and not filled_down:
                entry_cancelled = True

        ub = s.get("up_book") or {}
        db = s.get("down_book") or {}
        mid = _mid(ub)
        # --- ENTRY ANCHOR (issue #225) ---
        # Two settled points, both of which this used to get wrong.
        #
        # 1. Repriced on every tick until the quote is actually live, so the
        #    price that reaches the book is the mid at placement time. The old
        #    `if resting_up is None` anchored once at delay expiry and never
        #    again: whenever anything held placement -- a failing queue gate,
        #    a cancelled window later re-entered -- live kept tracking the
        #    mid while this stayed frozen on a mid from ticks ago. They
        #    agreed only when placement happened on the very tick the delay
        #    expired.
        #
        # 2. The anchor is the two-sided mid and nothing else. `s["mid"]` is the
        #    collector's up-leg reading and survives a one-sided down book, so
        #    preferring it quoted a book that priced only one leg -- the same
        #    substitution issue #207 removed from live, on the other side. No
        #    two-sided mid means no anchor: an already-live quote stands (the
        #    order is on the venue), and an unplaced one simply waits.
        delay_expired = entry_delay <= 0 or elapsed >= entry_delay
        anchor_mid = _two_sided_mid(ub, db)
        # `entry_cancelled` is live's cancelled-orders state: the handles are
        # gone, so the anchor tracks the mid again and a later re-entry quotes
        # at the price of its own tick.
        if (not in_dead_zone and not filled_up and not filled_down and delay_expired
                and (entry_cancelled or not orders_live)
                and anchor_mid is not None):
            resting_up = round(min(0.99, max(0.01, anchor_mid - params.offset)), 3)
            resting_down = round(min(0.99, max(0.01, (1.0 - anchor_mid) - params.offset)), 3)
        # Live holds placement on a tick it cannot price (`no_book_hold`); a
        # quote already resting is unaffected, because it is already on the book.
        no_book_hold = (not orders_live) and anchor_mid is None
        if mid is None:
            continue
        mids.append(mid)
        if mid - 0.50 > max_up:
            max_up = mid - 0.50
        if 0.50 - mid > max_down:
            max_down = 0.50 - mid

        # Position excursion, measured from the filled leg's own entry price
        # (issue #209). Rounded to 6dp because the raw subtraction
        # (0.45 - 0.40 == 0.04999999999999999) sits a hair under an exactly
        # equal threshold and would silently skip the stop. With no leg filled
        # there is no position to stop out, so both stay at 0.0.
        #
        # "reversal_seen_<side>" = mid has come back toward the entry after
        # exceeding exit_thr on the adverse side -- the current drift is no
        # longer monotonic, so don't exit. E.g. if the down excursion passed
        # the threshold and the mid is now back within exit_reversal of the
        # entry, it was a round-trip and the adverse drift is not sustained.
        #
        # Issue #230: Latched against `exit_thr`, the single stop threshold.
        if filled_up and not filled_down:
            _entry_up = entry_price_up if entry_price_up is not None else resting_up
            if _entry_up is not None:
                _excursion_down = round(_entry_up - mid, 6)
                adverse_drift_down = max(adverse_drift_down, _excursion_down)
                if adverse_drift_down >= exit_thr and _excursion_down < params.exit_reversal:
                    reversal_seen_down = True
        elif filled_down and not filled_up:
            _entry_dn = entry_price_down if entry_price_down is not None else resting_down
            if _entry_dn is not None:
                _excursion_up = round(mid - (1.0 - _entry_dn), 6)
                adverse_drift_up = max(adverse_drift_up, _excursion_up)
                if adverse_drift_up >= exit_thr and _excursion_up < params.exit_reversal:
                    reversal_seen_up = True

        # --- QUOTABLE RANGE (issue #228) ---
        # One range, judged every tick on the anchor's own two-sided mid and
        # latching nothing (operator-approved: no second mid computation). A
        # mid outside [quote_lo, quote_hi] holds placement for that tick only
        # (`range_hold`, same shape as `no_book_hold`): an already-resting
        # quote stands because it is on the venue, and a filled leg's chase
        # and exits are not entry. No two-sided mid means no judgement — the
        # existing `no_book_hold` covers that tick. A market that leaves the
        # range and returns is quoted again in the same window.
        range_hold = (
            (not orders_live)
            and anchor_mid is not None
            and (anchor_mid < quote_lo or anchor_mid > quote_hi)
        )

        # Queue gate (0 disables per Plan §2; max_rest_queue_ahead=0 means "always pass")
        if params.queue_gate <= 0:
            queue_ok = True
        elif resting_up is None or resting_down is None:
            # Delay not expired (or no usable mid yet): nothing quotable.
            # Same single fact as `quotable` below, expressed for the depth
            # filter, which needs a resting price to measure against.
            queue_ok = False
        else:
            # `queue_ahead` returns None for a book with no bids at all. That
            # used to sum to 0.0 here and pass the gate, i.e. an absent book
            # read as front-of-queue -- the reading issue #138 rejected in the
            # live engine. Unknown depth now fails the gate, matching live.
            q_up = book_math.queue_ahead(ub.get("bids"), resting_up)
            q_dn = book_math.queue_ahead(db.get("bids"), resting_down)
            queue_ok = (q_up is not None and q_dn is not None
                        and q_up <= params.queue_gate
                        and q_dn <= params.queue_gate)

        up_ask = ub.get("best_ask")
        dn_ask = db.get("best_ask")
        # Issue #227 deleted the entry-side pair-cost test that stood here. It
        # measured our own resting quotes, which cost `1 - 2*offset` by
        # construction — a constant, not a fact about the market. It therefore
        # admitted every window or blocked every window depending only on the
        # offset, and when it blocked it skipped fill detection with the rest of
        # the branch. `max_pair_cost` now caps the chase and nothing else; the
        # offset alone decides what an entry costs.

        # --- LEG CHASE (issue #164, mirrors live issue #123 and sim2) ---
        # One leg filled: step the other toward its ask, floored to cent
        # precision so entry + opposite can never exceed `max_pair_cost`. The
        # quote is only ever raised — lowering it would walk away from a fill
        # already within reach. Runs before fill detection so a chase and its
        # fill can land on the same tick, as they do live.
        #
        # Placed ABOVE the entry gate deliberately. Completing a pair you are
        # already half into is not a new entry, so a book that no longer meets
        # the entry gate must not strand the open leg — the same reasoning the
        # exit check below is placed there for.
        # --- LEG CHASE AFTER ONE-SIDED FILL (rules §4, §8, §12) ---
        # Issue #231: The chase ceiling walks from original resting quote up to
        # max_affordable as time approaches the dead zone.
        # At progress == 0.0, ceiling == original_resting (patience on first tick).
        # At progress == 1.0 (dead zone), ceiling == max_affordable.
        chased_now_up = False
        chased_now_down = False
        if (params.enable_leg_chase and (filled_up != filled_down)
                and not pair_captured and not exit_taken
                and resting_up is not None and resting_down is not None):
            _cap = params.max_pair_cost
            _dz_cutoff = book_math.dead_zone_cutoff_seconds(
                window_length, params.dead_zone_val, params.dead_zone_unit)
            _dead_zone_start_sec = window_length - _dz_cutoff
            _went_naked = naked_since_elapsed if naked_since_elapsed is not None else elapsed
            _prog = book_math.chase_progress(elapsed, _went_naked, _dead_zone_start_sec)

            if filled_up:
                _ask = db.get("best_ask")
                if _ask is not None:
                    _entry = entry_price_up if entry_price_up is not None else resting_up
                    _max_bid = book_math.chase_cap(_cap, _entry)
                    _orig = original_resting_down if original_resting_down is not None else resting_down
                    _ceiling = book_math.chase_ceiling(_orig, _max_bid, _prog)
                    _target = min(_ask, _ceiling)
                    if _target > resting_down:
                        resting_down = round(min(0.99, max(0.01, _target)), 3)
                        chased_leg = "down"
                        chased_now_down = True
            else:
                _ask = ub.get("best_ask")
                if _ask is not None:
                    _entry = entry_price_down if entry_price_down is not None else resting_down
                    _max_bid = book_math.chase_cap(_cap, _entry)
                    _orig = original_resting_up if original_resting_up is not None else resting_up
                    _ceiling = book_math.chase_ceiling(_orig, _max_bid, _prog)
                    _target = min(_ask, _ceiling)
                    if _target > resting_up:
                        resting_up = round(min(0.99, max(0.01, _target)), 3)
                        chased_leg = "up"
                        chased_now_up = True


        if not queue_ok:
            if not filled_up and not filled_down:
                continue

        # --- FILL DETECTION (issue #226: one rule, `book_math.resting_bid_filled`) ---
        # A print at our price, or an ask fully through it. Both are detectors
        # of the same event -- our resting limit order being taken -- so the
        # fill price is always our own and no fee is charged here. The taker
        # fee belongs to the exits below, which cross the book to sell.
        # Hoist token lookups and guard empty identifiers (prevents "" == "" match).
        up_token = (first.get("up_token") or (ub.get("token_id") or "")).strip()
        dn_token = (first.get("down_token") or (db.get("token_id") or "")).strip()
        quotable = (resting_up is not None and resting_down is not None
                    and not range_hold and not no_book_hold)
        can_fill_up = (not filled_up) and (not in_dead_zone or filled_down) and (not entry_cancelled or filled_down) and quotable
        can_fill_down = (not filled_down) and (not in_dead_zone or filled_up) and (not entry_cancelled or filled_up) and quotable
        # A quote that is not live yet is being *placed* on this tick, so it
        # can be marketable on arrival (issue #226). One already resting has to
        # wait for the ask to pass fully through it.
        placed_now = not orders_live
        if (quotable and (can_fill_up or can_fill_down)) or filled_up or filled_down:
            window_entered = True
            # The quote reached the book on this tick, so from the next one it
            # latches (#225) -- exactly as live stops repricing once an order id
            # exists. A later cancellation re-opens repricing via
            # `entry_cancelled` in the anchor block above.
            orders_live = True
            if original_resting_up is None:
                original_resting_up = resting_up
            if original_resting_down is None:
                original_resting_down = resting_down

        # Raw JSON values, not floats: `book_math._as_price` is what decides
        # whether a recorded price is usable at all.
        up_prints: list[Any] = []
        dn_prints: list[Any] = []
        for trade in s.get("tape_delta") or []:
            tasset = str(trade.get("asset", "")).strip()
            if not tasset:
                continue
            if up_token and tasset == up_token:
                up_prints.append(trade.get("price"))
            elif dn_token and tasset == dn_token:
                dn_prints.append(trade.get("price"))
        if can_fill_up and book_math.resting_bid_filled(
                resting_up, up_ask, up_prints, params.tick_size,
                newly_placed=placed_now or chased_now_up):
            filled_up = True
            can_fill_up = False
        if can_fill_down and book_math.resting_bid_filled(
                resting_down, dn_ask, dn_prints, params.tick_size,
                newly_placed=placed_now or chased_now_down):
            filled_down = True
            can_fill_down = False

        # Latch each leg's entry price the tick it fills. Without this, a
        # chase step after the fill would rewrite the entry the P&L is
        # computed against (issue #164).
        if filled_up and entry_price_up is None:
            entry_price_up = resting_up
        if filled_down and entry_price_down is None:
            entry_price_down = resting_down

        # --- PAIR COMPLETION ---
        if filled_up and filled_down and not pair_captured and not exit_taken:
            pair_captured = True
            pnl_cents += (1.00 - (resting_up + resting_down)) * 100.0
            # merge_gas_usd is a per-transaction cost; amortize over the
            # actual shares in the pair so pnl_cents stays per-share.
            pnl_cents -= (params.merge_gas_usd * 100.0) / max(1, params.quote_shares)
            break

        # --- NAKED LEG CLOCK (issue #164, mirrors live `naked_since_ts`) ---
        # Starts when exactly one leg is filled and resets the moment that
        # stops being true, so a pair completing clears the timeout instead of
        # carrying a stale deadline.
        _one_leg = (filled_up != filled_down)
        if _one_leg and naked_since_elapsed is None:
            naked_since_elapsed = elapsed
        elif not _one_leg:
            naked_since_elapsed = None

        # --- DEAD ZONE: UNPAIRED LEG HANDLING (rules §8 and §14) ---
        # In the dead zone: cancel unfilled resting quotes. If one leg is filled,
        # close it at best bid if naked_leg_at_expiry == "close", or hold to settlement
        # if naked_leg_at_expiry == "hold".
        if in_dead_zone and _one_leg and not exit_taken and not pair_captured:
            entry_cancelled = True
            if params.naked_leg_at_expiry == "close":
                _book = ub if filled_up else db
                _bb = _quote(_book.get("best_bid"))
                if _bb is not None:
                    exit_taken = True
                    exit_side = "up" if filled_up else "down"
                    exit_price = _bb
                    _rest = resting_up if filled_up else resting_down
                    pnl_cents += (_bb - _rest) * 100.0
                    fees_cents += _taker_fee(_bb, params.taker_fee_rate) * 100.0
                    break

        # --- EXIT (one side filled, mid drifted past thresh without reversal) ---
        # Check BEFORE we update the reversal flag this tick so the crossing
        # tick is the exit tick (otherwise the flag toggles the same tick and
        # the exit is suppressed).
        # Issue #230: Governed by single `exit_thr`.
        if (filled_up and not filled_down and adverse_drift_down >= exit_thr
                and not reversal_seen_down and not exit_taken):
            bb_up = ub.get("best_bid")
            if bb_up is not None:
                exit_taken = True
                exit_side = "up"
                exit_price = bb_up
                pnl_cents += (bb_up - resting_up) * 100.0
                fees_cents += _taker_fee(bb_up, params.taker_fee_rate) * 100.0
                break
        if (filled_down and not filled_up and adverse_drift_up >= exit_thr
                and not reversal_seen_up and not exit_taken):
            bb_dn = db.get("best_bid")
            if bb_dn is not None:
                exit_taken = True
                exit_side = "down"
                exit_price = bb_dn
                pnl_cents += (bb_dn - resting_down) * 100.0
                fees_cents += _taker_fee(bb_dn, params.taker_fee_rate) * 100.0
                break

    # Fallback for windows where the chase never ran: the resting price at
    # loop end is the price the leg rested at throughout.
    if filled_up and entry_price_up is None:
        entry_price_up = resting_up
    if filled_down and entry_price_down is None:
        entry_price_down = resting_down

    if filled_up or filled_down:
        if (filled_up and not filled_down) or (filled_down and not filled_up):
            fees_cents += _taker_fee(0.50, params.taker_fee_rate) * 100.0
        # Value any still-open naked leg at window close (issue #191). This
        # used to stop at the held side's final best_bid and book 0.00c when it
        # was absent — which is precisely when the leg was worthless, so every
        # such window dropped a full loss and kept every win. The ladder in
        # `resolve_naked_settlement` mirrors live's `_resolve_exit_bid` and
        # redeems when no stage resolves. Taker fee is charged only on a real
        # mark; a redemption is not a closing trade. Pair-capture and exit
        # behaviour above are untouched.
        if not pair_captured and not exit_taken and (filled_up != filled_down):
            _resting = resting_up if filled_up else resting_down
            if _resting is not None:
                mark, delta, src = resolve_naked_settlement(
                    window_snaps, filled_up, _resting)
                settle_source = src
                if src != "unresolved":
                    pnl_cents += delta
                    settled_unmarked = src != "direct_bid"
                if mark is not None:
                    exit_price = mark
                    settlement_mid = mark
                    fees_cents += _taker_fee(mark, params.taker_fee_rate) * 100.0

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
        # Issue #228: the re-entry mechanism is deleted, so this is always
        # 0 (the field itself goes in T5 with its last readers).
        reentry_count=0,
        entry_price_up=entry_price_up,
        entry_price_down=entry_price_down,
        exit_price=exit_price,
        settlement_mid=settlement_mid,
        chased_leg=chased_leg,
        chased_resting=(resting_down if chased_leg == "down"
                        else resting_up if chased_leg == "up" else None),
        settled_unmarked=settled_unmarked,
        settle_source=settle_source,
        entered=window_entered,
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
        per_window.append(_simulate_window(group, params))

    per_series: dict[str, dict] = defaultdict(lambda: {
        "windows": 0, "entered": 0, "pair": 0, "exit": 0, "filled_up_only": 0,
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
            "settled_unmarked": w.settled_unmarked,
            "settle_source": w.settle_source,
            "pnl_cents": round(w.pnl_cents, 2),
            "exit_reason": exit_info,
            "start_delay_sec": w.start_delay_sec,
            "is_partial": w.is_partial,
        })

        # Per series tracking
        a = per_series[w.series]
        a["windows"] += 1
        if getattr(w, "entered", False):
            a["entered"] += 1
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
            "entered": d.get("entered", 0),
            "entered_windows": d.get("entered", 0),
            "entered_rate": round(d.get("entered", 0) / n, 4) if n else 0.0,
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
        "entered": sum(s.get("entered", 0) for s in per_series.values()),
        "entered_windows": sum(s.get("entered", 0) for s in per_series.values()),
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
