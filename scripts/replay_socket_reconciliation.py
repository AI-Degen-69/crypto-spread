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
            self.report.total_rest_snapshots += 1

    def _select_rest_book(
        self,
        tok: str,
        ws_rx: float,
        in_bb: Optional[float] = None,
        in_ba: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Select a temporally matched REST snapshot for tok at ws_rx."""
        snapshots = self.rest_snapshots.get(tok)
        if not snapshots:
            return self.rest_books.get(tok)

        if ws_rx <= 0.0:
            candidate = snapshots[-1].book
        else:
            best = min(snapshots, key=lambda s: abs(s.rx - ws_rx))
            candidate = best.book if abs(best.rx - ws_rx) <= 0.5 else None

        if candidate and (in_bb is not None or in_ba is not None):
            cand_bb = _num(candidate.get("best_bid"))
            cand_ba = _num(candidate.get("best_ask"))
            if (
                (in_bb is not None and cand_bb is not None and abs(in_bb - cand_bb) > TOLERANCE + EPSILON)
                or (in_ba is not None and cand_ba is not None and abs(in_ba - cand_ba) > TOLERANCE + EPSILON)
            ):
                return None

        return candidate

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
            rest_book = self._select_rest_book(tok, ws_rx, declared_bb, declared_ba)
            if rest_book:
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
                    if divergent_rest:
                        self.report.rest_divergences[ev_type] = (
                            self.report.rest_divergences.get(ev_type, 0) + 1
                        )
                        max_d_rest = max(d for d in (d_bb_rest, d_ba_rest) if d is not None)
                        self.report.max_gap_by_type[ev_type] = max(
                            self.report.max_gap_by_type.get(ev_type, 0.0), max_d_rest
                        )
                        div_rec_rest = DivergenceRecord(
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
    if fixture.get("breaking_event"):
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
