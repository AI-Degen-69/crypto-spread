"""Offline per-delta socket-book reconciliation replay engine.

Replays captured raw CLOB WebSocket events through `CLOBMarketWSClient` one by one,
reconciling the maintained book against:
1. In-frame venue declared quotes (`best_bid`, `best_ask` on `price_change` / `best_bid_ask`), and
2. Concurrent REST ground-truth book snapshots (`full_book()`).

Pinpoints exactly which event type causes order book divergence and extracts
the minimal breaking event sequence as a reproducible fixture.

Usage:
    python -m scripts.replay_socket_reconciliation run/diag_ws/raw_session_*.jsonl
    python -m scripts.replay_socket_reconciliation <file> --fixture-out tests/fixtures/socket_divergence_smoking_gun.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import collections
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from strategy.streaming import CLOBMarketWSClient  # noqa: E402


@dataclass
class RestSnapshotRecord:
    """Recorded REST snapshot with timestamp."""

    rx: float
    book: Dict[str, Any]


TOLERANCE = 0.001
EPSILON = TOLERANCE * 1e-6

#: One venue tick. Equal to TOLERANCE by construction (issue #438), and divergence
#: is defined as *strictly* greater than it -- so a gap of exactly one tick is not a
#: divergence, and anything at or below one tick is invisible to the divergence rule.
TICK = TOLERANCE

EXACT = "exact"
SUB_TICK = "sub_tick"
TICKS_1_3 = "1_3_ticks"
TICKS_OVER_3 = ">3_ticks"
MAGNITUDE_BUCKETS = (EXACT, SUB_TICK, TICKS_1_3, TICKS_OVER_3)

SKEW_REST_AFTER_WS = "rest_after_ws"
SKEW_REST_BEFORE_WS = "rest_before_ws"
SKEW_UNKNOWN = "unknown"

AGE_UNKNOWN = "unknown"
AGE_BUCKETS = ("le_50ms", "le_100ms", "le_250ms", "le_500ms", AGE_UNKNOWN)


def age_bucket(age: Optional[float]) -> str:
    """Bucket the age of the REST reference behind one comparison.

    Hypothesis 4 of #438 says a divergent comparison may be one correct book read
    at two instants, which is a claim about *staleness*, not about order. Recording
    only which read came first cannot test it when the ordering is one-sided, as it
    is on the first ground-truth capture; the age distribution can: if divergences
    sit in older buckets than the population they came from, staleness explains them
    and the metric is the thing at fault.
    """
    if age is None:
        return AGE_UNKNOWN
    if age <= 0.05:
        return "le_50ms"
    if age <= 0.1:
        return "le_100ms"
    if age <= 0.25:
        return "le_250ms"
    return "le_500ms"


def tick_bucket(gap: float) -> str:
    """Magnitude bucket for one comparison gap, expressed in venue ticks.

    `exact` and `sub_tick` together hold the population the divergence rule
    cannot see: no gap at or below one tick registers as divergent, because
    divergence requires strictly more than TOLERANCE. They are kept apart on
    purpose -- perfect agreement and sub-tick drift are different observations,
    and the issue's bimodality claim ("a large population of sub-tick drift plus
    a rare, violent population") cannot be tested if agreement is folded into the
    drift bucket. Bucketing *every* comparable pair, not only the divergent
    ones, is what makes that claim measurable instead of assumed.
    """
    if gap <= 0.0:
        return EXACT
    if gap <= TICK:
        return SUB_TICK
    if gap <= 3 * TICK:
        return TICKS_1_3
    return TICKS_OVER_3


def _num(v: Any) -> Optional[float]:
    """Parse numeric value safely to float or None."""
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


@dataclass
class DivergenceRecord:
    """Structured divergence observation comparing WS state to ground truth."""

    event_index: int
    event_type: str
    token: str
    source: str  # "in_frame" or "rest"
    ws_bb: Optional[float]
    ws_ba: Optional[float]
    ref_bb: Optional[float]
    ref_ba: Optional[float]
    bb_delta: Optional[float]
    ba_delta: Optional[float]
    max_gap: float
    event: Dict[str, Any]
    preceding_events: List[Dict[str, Any]]
    book_snapshot_before: Optional[Dict[str, Any]] = None
    ws_rx: float = 0.0
    rest_rx: Optional[float] = None
    tick_bucket: str = ""
    age_s: Optional[float] = None


@dataclass
class ReconciliationReport:
    """Aggregated reconciliation results and divergence metrics."""

    total_lines: int = 0
    total_ws_events: int = 0
    total_rest_snapshots: int = 0
    events_by_type: Dict[str, int] = field(default_factory=dict)
    comparisons_by_type: Dict[str, int] = field(default_factory=dict)
    in_frame_divergences: Dict[str, int] = field(default_factory=dict)
    rest_divergences: Dict[str, int] = field(default_factory=dict)
    max_gap_by_type: Dict[str, float] = field(default_factory=dict)
    first_divergence: Optional[DivergenceRecord] = None
    all_divergences: List[DivergenceRecord] = field(default_factory=list)
    magnitude_buckets: Dict[str, int] = field(default_factory=dict)
    skew_buckets: Dict[str, int] = field(default_factory=dict)
    age_buckets_all: Dict[str, int] = field(default_factory=dict)
    age_buckets_divergent: Dict[str, int] = field(default_factory=dict)
    per_series: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def note_magnitude(self, gap: float) -> None:
        """Count one comparable pair into its magnitude bucket (issue #438)."""
        key = tick_bucket(gap)
        self.magnitude_buckets[key] = self.magnitude_buckets.get(key, 0) + 1

    def note_series(self, series: str, divergent: bool,
                    gap: Optional[float] = None) -> None:
        """Count one REST comparison into its series' row.

        The per-series split is the deliverable: the issue records 5m series
        diverging at 26-29% while `btc-15m` sits at 5.2%, and a single global
        rate hides exactly that spread. `gap` is optional because a pair whose
        two books carry disjoint quote fields has no measurable gap, yet is
        still a comparison in the denominator.
        """
        row = self.per_series.setdefault(series, {
            "comparisons": 0, "rest_divergences": 0,
            "divergence_rate": 0.0, "max_gap": 0.0,
        })
        row["comparisons"] += 1
        if divergent:
            row["rest_divergences"] += 1
        if gap is not None:
            row["max_gap"] = max(row["max_gap"], gap)
        row["divergence_rate"] = round(row["rest_divergences"] / row["comparisons"], 4)

    def note_skew(self, rest_rx: Optional[float], ws_rx: float) -> None:
        """Classify whether the REST read happened after the WS mutation.

        Hypothesis 4 of #438: a comparison whose REST snapshot postdates the WS
        event that set the top quote may be one correct book observed at two
        instants, not corruption. Counting the pairs this way is what rules the
        hypothesis in or out rather than arguing about it.
        """
        if rest_rx is None:
            key = SKEW_UNKNOWN
        elif rest_rx > ws_rx:
            key = SKEW_REST_AFTER_WS
        else:
            key = SKEW_REST_BEFORE_WS
        self.skew_buckets[key] = self.skew_buckets.get(key, 0) + 1

    def note_age(self, age: Optional[float], divergent: bool) -> None:
        """Histogram the REST reference's age, for the population and for divergences.

        Two histograms rather than one: the hypothesis is comparative (are divergent
        pairs older than the pairs around them?), so the denominator has to be
        recorded alongside the numerator (issue #438, T3b).
        """
        key = age_bucket(age)
        self.age_buckets_all[key] = self.age_buckets_all.get(key, 0) + 1
        if divergent:
            self.age_buckets_divergent[key] = self.age_buckets_divergent.get(key, 0) + 1

    def summary_dict(self) -> Dict[str, Any]:
        """Generate serializable summary breakdown by event type."""
        types = sorted(set(self.events_by_type.keys()) | set(self.comparisons_by_type.keys()))
        type_stats = {}
        for t in types:
            ev_count = self.events_by_type.get(t, 0)
            comp_count = self.comparisons_by_type.get(t, 0)
            in_div = self.in_frame_divergences.get(t, 0)
            rest_div = self.rest_divergences.get(t, 0)
            max_gap = self.max_gap_by_type.get(t, 0.0)
            div_rate = round(rest_div / comp_count, 4) if comp_count > 0 else 0.0
            in_rate = round(in_div / ev_count, 4) if ev_count > 0 else 0.0
            type_stats[t] = {
                "events": ev_count,
                "comparisons": comp_count,
                "in_frame_divergences": in_div,
                "in_frame_rate": in_rate,
                "rest_divergences": rest_div,
                "rest_divergence_rate": div_rate,
                "max_gap": round(max_gap, 4),
            }
        return {
            "total_lines": self.total_lines,
            "total_ws_events": self.total_ws_events,
            "total_rest_snapshots": self.total_rest_snapshots,
            "by_type": type_stats,
            "magnitude_buckets": dict(self.magnitude_buckets),
            "skew_buckets": dict(self.skew_buckets),
            "age_buckets_all": dict(self.age_buckets_all),
            "age_buckets_divergent": dict(self.age_buckets_divergent),
            "per_series": {k: dict(v) for k, v in self.per_series.items()},
            "first_divergence": (
                {
                    "event_index": self.first_divergence.event_index,
                    "event_type": self.first_divergence.event_type,
                    "token": self.first_divergence.token,
                    "source": self.first_divergence.source,
                    "ws_bb": self.first_divergence.ws_bb,
                    "ws_ba": self.first_divergence.ws_ba,
                    "ref_bb": self.first_divergence.ref_bb,
                    "ref_ba": self.first_divergence.ref_ba,
                    "max_gap": round(self.first_divergence.max_gap, 4),
                }
                if self.first_divergence
                else None
            ),
        }


class SocketReconciler:
    """Offline replay engine driving CLOBMarketWSClient from recorded streams."""

    def __init__(self, history_buffer_size: int = 15,
                 token_ids: Optional[List[str]] = None):
        """Initialize offline reconciler with local WS client and state cache.

        `token_ids` scopes the seeded client to one fixture token so sibling legs
        in the same frame cannot create books (Issue #362).
        """
        self.client = CLOBMarketWSClient(
            token_ids=list(token_ids)) if token_ids else CLOBMarketWSClient()
        self.rest_books: Dict[str, Dict[str, Any]] = {}
        self.rest_snapshots: Dict[str, List[RestSnapshotRecord]] = collections.defaultdict(list)
        self.report = ReconciliationReport()
        self.history_buffer_size = history_buffer_size
        self._recent_events: List[Dict[str, Any]] = []
        #: token -> series slug, learned from REST records (WS frames name only
        #: tokens). A token with no REST record has no series to attribute.
        self.token_series: Dict[str, str] = {}

    def feed_line(self, line: str) -> None:
        """Parse and route one JSONL line from a capture session."""
        self.report.total_lines += 1
        line = line.strip()
        if not line:
            return
        try:
            record = json.loads(line)
        except Exception:
            return

        rec_type = record.get("type")
        if rec_type == "rest":
            self._handle_rest_snapshot(record)
        elif rec_type == "ws":
            self._handle_ws_event(record)
        elif "price_changes" in record or "bids" in record or "event_type" in record:
            # Standalone raw event format
            self._handle_ws_event({"type": "ws", "ev": record, "rx": 0.0})

    def _handle_rest_snapshot(self, record: Dict[str, Any]) -> None:
        """Store REST snapshot book as ground truth reference."""
        tok = str(record.get("token") or "")
        book = record.get("book") or {}
        rx = _num(record.get("rx")) or 0.0
        if tok and book:
            self.rest_books[tok] = book
            self.rest_snapshots[tok].append(RestSnapshotRecord(rx=rx, book=book))
            series = str(record.get("series") or "")
            if series:
                self.token_series[tok] = series
            self.report.total_rest_snapshots += 1

    def _select_rest_book(
        self,
        tok: str,
        ws_rx: float,
        in_bb: Optional[float] = None,
        in_ba: Optional[float] = None,
    ) -> Optional[Tuple[Dict[str, Any], Optional[float]]]:
        """Select a temporally matched REST snapshot for tok at ws_rx.

        Returns the book together with the snapshot's receive time, so a caller
        can tell sampling skew (the REST read landed after the WS mutation it is
        compared against) from genuine disagreement (issue #438). The
        in-frame-disagreement guard is unchanged: a REST read that contradicts
        the frame's own declared quotes is skipped rather than counted.
        """
        snapshots = self.rest_snapshots.get(tok)
        if not snapshots:
            fallback = self.rest_books.get(tok)
            return (fallback, None) if fallback else None

        if ws_rx <= 0.0:
            candidate, cand_rx = snapshots[-1].book, snapshots[-1].rx
        else:
            best = min(snapshots, key=lambda s: abs(s.rx - ws_rx))
            if abs(best.rx - ws_rx) > 0.5:
                return None
            candidate, cand_rx = best.book, best.rx

        if candidate and (in_bb is not None or in_ba is not None):
            cand_bb = _num(candidate.get("best_bid"))
            cand_ba = _num(candidate.get("best_ask"))
            if (
                (in_bb is not None and cand_bb is not None and abs(in_bb - cand_bb) > TOLERANCE + EPSILON)
                or (in_ba is not None and cand_ba is not None and abs(in_ba - cand_ba) > TOLERANCE + EPSILON)
            ):
                return None

        return candidate, cand_rx

    def _handle_ws_event(self, record: Dict[str, Any]) -> None:
        """Process WS message, update local book, and check against references."""
        ev = record.get("ev") or {}
        if not isinstance(ev, dict):
            return

        self.report.total_ws_events += 1
        ws_rx = _num(record.get("rx")) or 0.0
        ev_type = str(ev.get("event_type") or ev.get("type") or "unknown").lower()
        self.report.events_by_type[ev_type] = self.report.events_by_type.get(ev_type, 0) + 1

        # Keep rolling window of preceding events for smoking-gun fixture
        history_copy = list(self._recent_events)
        self._recent_events.append(ev)
        if len(self._recent_events) > self.history_buffer_size:
            self._recent_events.pop(0)

        # Tokens affected by this event
        affected_tokens: List[Tuple[str, Optional[float], Optional[float]]] = []

        if ev_type == "price_change":
            entries = ev.get("price_changes") or ev.get("changes") or []
            frame_tok = str(ev.get("asset_id") or ev.get("token_id") or "").strip()
            for c in entries:
                if isinstance(c, dict):
                    t = str(c.get("asset_id") or frame_tok or "").strip()
                    if t:
                        bb = _num(c.get("best_bid"))
                        ba = _num(c.get("best_ask"))
                        affected_tokens.append((t, bb, ba))
        else:
            t = str(ev.get("asset_id") or ev.get("token_id") or "").strip()
            if t:
                bb = _num(ev.get("best_bid")) if ev_type == "best_bid_ask" else None
                ba = _num(ev.get("best_ask")) if ev_type == "best_bid_ask" else None
                affected_tokens.append((t, bb, ba))

        snapshots_before: Dict[str, Optional[Dict[str, Any]]] = {
            tok: self.client.book_snapshot(tok) for tok, _bb, _ba in affected_tokens
        }

        # Dispatch event into client
        self.client._handle_event(ev)

        # Reconcile each affected token
        for tok, declared_bb, declared_ba in affected_tokens:
            ws_book = self.client.book_snapshot(tok)
            if not ws_book:
                continue

            ws_bb = _num(ws_book.get("best_bid"))
            ws_ba = _num(ws_book.get("best_ask"))

            # 1. In-Frame reconciliation
            if declared_bb is not None or declared_ba is not None:
                d_bb = abs(ws_bb - declared_bb) if (ws_bb is not None and declared_bb is not None) else None
                d_ba = abs(ws_ba - declared_ba) if (ws_ba is not None and declared_ba is not None) else None
                deltas_in = [d for d in (d_bb, d_ba) if d is not None]
                if deltas_in:
                    # Every comparable pair is bucketed, not only the divergent ones
                    # (issue #438): the sub-tick population is otherwise invisible.
                    self.report.note_magnitude(max(deltas_in))
                divergent_in = any(d is not None and d > TOLERANCE + EPSILON for d in (d_bb, d_ba))
                if divergent_in:
                    self.report.in_frame_divergences[ev_type] = (
                        self.report.in_frame_divergences.get(ev_type, 0) + 1
                    )
                    max_d = max(d for d in (d_bb, d_ba) if d is not None)
                    self.report.max_gap_by_type[ev_type] = max(
                        self.report.max_gap_by_type.get(ev_type, 0.0), max_d
                    )
                    div_rec = DivergenceRecord(
                        ws_rx=ws_rx,
                        tick_bucket=tick_bucket(max_d),
                        event_index=self.report.total_ws_events,
                        event_type=ev_type,
                        token=tok,
                        source="in_frame",
                        ws_bb=ws_bb,
                        ws_ba=ws_ba,
                        ref_bb=declared_bb,
                        ref_ba=declared_ba,
                        bb_delta=d_bb,
                        ba_delta=d_ba,
                        max_gap=max_d,
                        event=ev,
                        preceding_events=history_copy,
                        book_snapshot_before=snapshots_before.get(tok),
                    )
                    if not self.report.first_divergence:
                        self.report.first_divergence = div_rec
                    self.report.all_divergences.append(div_rec)

            # 2. Concurrent REST reconciliation
            selected = self._select_rest_book(tok, ws_rx, declared_bb, declared_ba)
            if selected:
                rest_book, rest_rx = selected
                rest_bb = _num(rest_book.get("best_bid"))
                rest_ba = _num(rest_book.get("best_ask"))
                if (rest_bb is not None or rest_ba is not None) and (ws_bb is not None or ws_ba is not None):
                    self.report.comparisons_by_type[ev_type] = (
                        self.report.comparisons_by_type.get(ev_type, 0) + 1
                    )
                    d_bb_rest = (
                        abs(ws_bb - rest_bb) if (ws_bb is not None and rest_bb is not None) else None
                    )
                    d_ba_rest = (
                        abs(ws_ba - rest_ba) if (ws_ba is not None and rest_ba is not None) else None
                    )
                    divergent_rest = any(
                        d is not None and d > TOLERANCE + EPSILON for d in (d_bb_rest, d_ba_rest)
                    )
                    deltas_rest = [d for d in (d_bb_rest, d_ba_rest) if d is not None]
                    gap_rest = max(deltas_rest) if deltas_rest else None
                    age_s = (ws_rx - rest_rx) if (rest_rx is not None and ws_rx > 0) else None
                    if gap_rest is not None:
                        self.report.note_magnitude(gap_rest)
                        self.report.note_skew(rest_rx, ws_rx)
                    self.report.note_series(
                        self.token_series.get(tok, "unknown"), divergent_rest, gap_rest)
                    self.report.note_age(age_s, divergent_rest)
                    if divergent_rest:
                        self.report.rest_divergences[ev_type] = (
                            self.report.rest_divergences.get(ev_type, 0) + 1
                        )
                        max_d_rest = max(deltas_rest)
                        self.report.max_gap_by_type[ev_type] = max(
                            self.report.max_gap_by_type.get(ev_type, 0.0), max_d_rest
                        )
                        div_rec_rest = DivergenceRecord(
                            ws_rx=ws_rx,
                            rest_rx=rest_rx,
                            tick_bucket=tick_bucket(max_d_rest),
                            age_s=age_s,
                            event_index=self.report.total_ws_events,
                            event_type=ev_type,
                            token=tok,
                            source="rest",
                            ws_bb=ws_bb,
                            ws_ba=ws_ba,
                            ref_bb=rest_bb,
                            ref_ba=rest_ba,
                            bb_delta=d_bb_rest,
                            ba_delta=d_ba_rest,
                            max_gap=max_d_rest,
                            event=ev,
                            preceding_events=history_copy,
                            book_snapshot_before=snapshots_before.get(tok),
                        )
                        if not self.report.first_divergence:
                            self.report.first_divergence = div_rec_rest
                        self.report.all_divergences.append(div_rec_rest)


def replay_file(file_path: Path, history_buffer_size: int = 15) -> ReconciliationReport:
    """Replay a recorded diagnostic capture file and return report."""
    reconciler = SocketReconciler(history_buffer_size=history_buffer_size)
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            reconciler.feed_line(line)
    return reconciler.report


FIXTURE_MAX_BYTES = 4 * 1024 * 1024  # extracted fixtures are ~16 KB; never slurp a session


def _load_fixture_object(path: Path) -> Optional[Dict[str, Any]]:
    """Return the fixture object stored in `path`, or None for a JSONL session.

    Session files can be hundreds of megabytes, so the check is cheap: the first
    non-empty line parsing as a JSON object means JSONL. Only otherwise is the
    whole file parsed — and only under `FIXTURE_MAX_BYTES` — returning the dict
    when it holds a `breaking_event` (Issue #362).
    """
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        first = ""
        for line in f:
            if line.strip():
                first = line
                break
        else:
            return None
    try:
        head = json.loads(first)
        # A compact single-line fixture parses but must not be mistaken for a
        # session: return it when it carries the fixture marker (Issue #363).
        if isinstance(head, dict) and "breaking_event" in head:
            return head
        return None
    except Exception:
        pass
    if path.stat().st_size > FIXTURE_MAX_BYTES:
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None
    if isinstance(obj, dict) and "breaking_event" in obj:
        return obj
    return None


def replay_fixture(file_path: Path) -> ReconciliationReport:
    """Replay one extracted smoking-gun fixture object and return the report.

    Seeds the client book from `book_snapshot_before` (which already encodes the
    effect of `preceding_events`, so those are never re-dispatched) and runs only
    `breaking_event` through the existing WS-event path, scoped to the fixture
    token (Issue #362).

    A fixture whose divergence was measured against REST also carries
    `rest_reference` and `ws_rx`; replaying those first is what lets such a
    fixture reproduce its own failure, since the comparison happens against the
    reference rather than against the frame's own declared quotes (Issue #438).
    """
    fixture = json.loads(Path(file_path).read_text(encoding="utf-8", errors="replace"))
    token = str(fixture.get("token") or "")
    reconciler = SocketReconciler(token_ids=[token] if token else None)
    before = fixture.get("book_snapshot_before") or {}
    if token and before:
        reconciler.client.apply_book_snapshot(
            token,
            [{"price": p, "size": s} for p, s in (before.get("bids") or {}).items()],
            [{"price": p, "size": s} for p, s in (before.get("asks") or {}).items()],
        )
    reference = fixture.get("rest_reference") or {}
    if token and reference.get("book"):
        reconciler.feed_line(json.dumps({
            "type": "rest",
            "token": token,
            "rx": reference.get("rx"),
            "book": reference["book"],
        }))
    if fixture.get("breaking_event"):
        ws_rx = _num(fixture.get("ws_rx"))
        if ws_rx:
            # Carry the WS receive time, or the comparison has no age to report.
            reconciler.feed_line(json.dumps({
                "type": "ws", "rx": ws_rx, "ev": fixture["breaking_event"],
            }))
        else:
            reconciler.feed_line(json.dumps(fixture["breaking_event"]))
    return reconciler.report


def print_report_table(report: ReconciliationReport) -> None:
    """Print ASCII table summarizing the per-event attribution."""
    print("=" * 80)
    print("PER-DELTA SOCKET-BOOK RECONCILIATION REPORT (Issue #359)")
    print("=" * 80)
    print(f"Total lines processed:      {report.total_lines}")
    print(f"Total WS events:            {report.total_ws_events}")
    print(f"Total REST snapshots:       {report.total_rest_snapshots}")
    print(f"Total divergences recorded: {len(report.all_divergences)}")
    print("-" * 80)
    header = f"{'Event Type':<18} | {'Events':<8} | {'In-Frame Div':<12} | {'REST Comps':<10} | {'REST Div':<9} | {'REST Rate':<9} | {'Max Gap'}"
    print(header)
    print("-" * 80)

    summary = report.summary_dict()["by_type"]
    for ev_type, s in sorted(summary.items(), key=lambda kv: kv[1]["rest_divergences"], reverse=True):
        rate_str = f"{s['rest_divergence_rate'] * 100:.1f}%" if s["comparisons"] > 0 else "N/A"
        gap_str = f"${s['max_gap']:.4f}"
        print(
            f"{ev_type:<18} | {s['events']:<8} | {s['in_frame_divergences']:<12} | "
            f"{s['comparisons']:<10} | {s['rest_divergences']:<9} | {rate_str:<9} | {gap_str}"
        )
    if report.magnitude_buckets:
        print("-" * 80)
        print("MAGNITUDE BUCKETS (every comparable pair; one tick = $0.001):")
        for key in MAGNITUDE_BUCKETS:
            print(f"  {key:<12} {report.magnitude_buckets.get(key, 0)}")
    if report.skew_buckets:
        print("REST/WS TIMING ORDER (hypothesis 4: skew, not corruption):")
        for key in (SKEW_REST_AFTER_WS, SKEW_REST_BEFORE_WS, SKEW_UNKNOWN):
            print(f"  {key:<16} {report.skew_buckets.get(key, 0)}")
    if report.age_buckets_all:
        print("REST REFERENCE AGE (hypothesis 4: staleness, not corruption):")
        print(f"  {'bucket':<12} {'all':<8} {'divergent':<10} {'div rate'}")
        for key in AGE_BUCKETS:
            total = report.age_buckets_all.get(key, 0)
            if not total:
                continue
            div = report.age_buckets_divergent.get(key, 0)
            print(f"  {key:<12} {total:<8} {div:<10} {div / total * 100:.1f}%")
    if report.per_series:
        print("PER SERIES (REST comparisons):")
        for slug, row in sorted(report.per_series.items()):
            rate = f"{row['divergence_rate'] * 100:.1f}%"
            print(f"  {slug:<30} comps={row['comparisons']:<7} div={row['rest_divergences']:<6} "
                  f"rate={rate:<8} max_gap=${row['max_gap']:.4f}")
    print("=" * 80)

    if report.first_divergence:
        fd = report.first_divergence
        print("\nFIRST DETECTED DIVERGENCE (SMOKING GUN):")
        print(f"  Event #{fd.event_index} | Type: {fd.event_type} | Token: {fd.token}")
        print(f"  Source: {fd.source} | Max gap: ${fd.max_gap:.4f}")
        print(f"  WS Reconstructed:  best_bid={fd.ws_bb} | best_ask={fd.ws_ba}")
        print(f"  Reference Ground:  best_bid={fd.ref_bb} | best_ask={fd.ref_ba}")
        print(f"  Breaking Event Payload:\n    {json.dumps(fd.event, indent=2)}")
    else:
        print("\nNo divergences detected across the dataset (all books agreed within tolerance).")


def extract_fixture(report: ReconciliationReport, out_path: Path) -> bool:
    """Save the first breaking sequence as a JSON fixture."""
    if not report.first_divergence:
        return False
    fd = report.first_divergence
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fixture_data = {
        "description": "Minimal breaking event sequence extracted from recorded session (Issue #359)",
        "event_type": fd.event_type,
        "token": fd.token,
        "source": fd.source,
        "ws_before": {"best_bid": fd.ws_bb, "best_ask": fd.ws_ba},
        "reference_ground": {"best_bid": fd.ref_bb, "best_ask": fd.ref_ba},
        "max_gap": round(fd.max_gap, 4),
        "book_snapshot_before": fd.book_snapshot_before,
        "breaking_event": fd.event,
        "preceding_events": fd.preceding_events,
    }
    # Issue #438: a divergence measured against REST is only reproducible if the
    # fixture carries that reference and the times it was compared across. The
    # two fields are added only for REST-sourced divergences, so the #359 fixture
    # shape (an in-frame divergence against the frame's own quotes) is unchanged.
    if fd.source == "rest" and fd.rest_rx is not None:
        # best_bid/best_ask are the only fields the divergence rule reads, so the
        # fixture stores exactly what the comparison used rather than a book's depth.
        fixture_data["rest_reference"] = {
            "rx": fd.rest_rx,
            "book": {"best_bid": fd.ref_bb, "best_ask": fd.ref_ba},
        }
        fixture_data["ws_rx"] = fd.ws_rx
        fixture_data["age_s"] = round(fd.age_s, 6) if fd.age_s is not None else None
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(fixture_data, f, indent=2)
    print(f"Saved breaking fixture to {out_path}")
    return True


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point for offline reconciliation replay."""
    parser = argparse.ArgumentParser(description="Offline per-delta socket-book reconciliation replay")
    parser.add_argument("file", type=str, help="Path to raw session JSONL file")
    parser.add_argument("--fixture-out", type=str, default=None, help="Path to write smoking gun fixture")
    args = parser.parse_args(argv)

    path = Path(args.file)
    if not path.exists():
        print(f"File not found: {path}")
        return 1

    fixture = _load_fixture_object(path)
    if fixture is not None:
        if not fixture.get("breaking_event"):
            print(f"Fixture object in {path} has no breaking_event to replay")
            return 2
        print(f"Replaying extracted fixture object from {path} (Issue #362)")
        report = replay_fixture(path)
    else:
        report = replay_file(path)
    print_report_table(report)

    if args.fixture_out:
        extract_fixture(report, Path(args.fixture_out))

    return 0


if __name__ == "__main__":
    sys.exit(main())
