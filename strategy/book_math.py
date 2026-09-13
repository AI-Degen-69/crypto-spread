"""Order-book arithmetic shared by every consumer of market data (issue #170).

The collector, the backtest engine, the live/paper trader and the sweep lab all
have to answer the same two questions about a book -- what is its mid, and how
much size rests ahead of our price -- and before this module each of them
answered differently. The copies agreed on healthy books and diverged on
exactly the degenerate ones that decide entries:

- `mid` on a one-sided book: the collector clamped into [0, 1], the engine did
  not, so a lone bid of 0.998 was recorded as a mid of 1.0 and replayed as
  1.003. These are binary outcome tokens; 1.003 is not a probability, and it
  feeds `resting = mid - offset`.
- `queue_ahead` on an empty book: the live engine returned None from issue #138
  onward, explicitly so "a degenerate book cannot masquerade as front-of-queue".
  Everywhere else returned 0.0, and `0.0 <= queue_gate` passes -- so the
  backtest took entries on books with no bids that live would have refused.
- `two_sided_mid` on a one-sided leg: the engine returned None while live
  substituted `bid or ask or 0.50`, despite the engine's own docstring claiming
  the two agreed.

Books reach these functions from three places with different typing: live REST
and WebSocket payloads (float keys), and tick files replayed from JSON (string
keys). Every function here accepts both and treats anything unparseable as
unknown rather than as zero.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

# A lone bid implies the true mid sits somewhere above it, and a lone ask
# implies it sits below. Half a cent is the smallest nudge that expresses the
# direction without pretending to know the spread.
ONE_SIDED_NUDGE = 0.005


def _as_price(value: Any) -> Optional[float]:
    """Coerce a venue- or JSON-supplied price to float, or None if it is junk."""
    if value is None:
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if price != price or price in (float("inf"), float("-inf")):
        return None  # NaN / Infinity survive json.loads and only break later
    return price


def clamp_probability(price: float) -> float:
    """Hold a price inside [0, 1]; these are binary outcome tokens."""
    return min(1.0, max(0.0, price))


def mid(book: Optional[Dict[str, Any]]) -> Optional[float]:
    """Midpoint of one leg's book, or None when the book prices nothing.

    Always clamped into [0, 1]: a one-sided book near an edge, or a malformed
    venue quote, must not produce a "probability" outside the range and feed it
    into a resting price.
    """
    if not book:
        return None
    best_bid = _as_price(book.get("best_bid"))
    best_ask = _as_price(book.get("best_ask"))
    if best_bid is not None and best_ask is not None:
        return clamp_probability((best_bid + best_ask) / 2.0)
    if best_bid is not None:
        return clamp_probability(best_bid + ONE_SIDED_NUDGE)
    if best_ask is not None:
        return clamp_probability(best_ask - ONE_SIDED_NUDGE)
    return None


def two_sided_mid(up_book: Optional[Dict[str, Any]],
                  down_book: Optional[Dict[str, Any]]) -> Optional[float]:
    """Synthetic mid across both legs, or None when either leg is one-sided.

    None is the honest answer for a leg that cannot be priced. Substituting a
    default (live used `bid or ask or 0.50`) reads downstream as "perfectly
    balanced", which is precisely what the adverse-open gate (#92) exists to
    catch -- a thin one-sided open would sail through it.
    """
    up_book = up_book or {}
    down_book = down_book or {}
    quotes = [_as_price(up_book.get("best_bid")), _as_price(up_book.get("best_ask")),
              _as_price(down_book.get("best_bid")), _as_price(down_book.get("best_ask"))]
    if any(q is None for q in quotes):
        return None
    up_mid = (quotes[0] + quotes[1]) / 2.0
    down_mid = (quotes[2] + quotes[3]) / 2.0
    return round(clamp_probability((up_mid + (1.0 - down_mid)) / 2.0), 4)


def queue_ahead(bids: Optional[Dict[Any, Any]],
                price: float) -> Optional[float]:
    """Size resting at or above `price` -- our queue position at rest.

    None means "unknown", never zero. An empty or missing book is the absence
    of depth data, not the presence of an empty queue, and reading it as zero
    puts us at the front of a queue we cannot see (issue #138). `0.0` is
    returned only for a real book that genuinely has nothing at or above us.
    """
    if not bids:
        return None
    target = _as_price(price)
    if target is None:
        return None
    total = 0.0
    for level, size in bids.items():
        level_price = _as_price(level)
        level_size = _as_price(size)
        if level_price is None or level_size is None or level_size < 0:
            return None  # a book we cannot parse is unknown, not empty
        if level_price >= target:
            total += level_size
    return total


def pair_cost(up: Any, down: Any) -> Optional[float]:
    """Combined cost of both resting legs, or None when either is unknown."""
    up_price = _as_price(up)
    down_price = _as_price(down)
    if up_price is None or down_price is None:
        return None
    return round(up_price + down_price, 4)


def two_sided_mid_with_default(up_book: Optional[Dict[str, Any]],
                               down_book: Optional[Dict[str, Any]],
                               default: float = 0.50) -> float:
    """The live engine's historical mid: substitutes `default` for a leg it
    cannot price, so the result is always a number.

    Deliberately NOT the same function as `two_sided_mid`, and deliberately
    named for what it does. Substituting 0.50 for an unpriceable leg reports
    "perfectly balanced" for a book that priced nothing, which is the reading
    the adverse-open gate (#92) exists to catch.

    It lives here only so the two live call sites share one copy instead of two
    inline ones. Issue #171 (WS/REST write-ordering race) kept this default-
    substitution semantic as-is -- moving to the honest `two_sided_mid` (and
    making `mstate.mid` Optional, which most of its consumers already guard
    for) changes live trading behaviour on its own and is out of scope here;
    revisit under #174 (full WS authority) instead.
    """
    up_book = up_book or {}
    down_book = down_book or {}

    def _leg(book: Dict[str, Any]) -> float:
        """One leg's mid, falling back to a single quote and then `default`."""
        best_bid = _as_price(book.get("best_bid"))
        best_ask = _as_price(book.get("best_ask"))
        if best_bid is not None and best_ask is not None:
            return (best_bid + best_ask) / 2.0
        # `bid or ask or default` -- the original spelling -- also discarded a
        # legitimate 0.0 quote as falsy. Explicit None checks keep it.
        if best_bid is not None:
            return best_bid
        if best_ask is not None:
            return best_ask
        return default

    return round((_leg(up_book) + (1.0 - _leg(down_book))) / 2.0, 4)
