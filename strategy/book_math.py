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

import math
from typing import Any, Dict, Iterable, Optional

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


def resting_bid_filled(resting: Any, best_ask: Any,
                       tape_prices: Iterable[Any], tick: float,
                       newly_placed: bool = False) -> bool:
    """Was our buy order filled on this tick? (issue #226)

    The single fill rule, shared by the backtest engine and the live trader's
    paper simulation so the two cannot drift apart. It answers *whether* we
    were filled, never at what price: the entry quote is a limit order that
    sits and waits, so whoever filled it was the aggressor. We are the maker,
    we get our own price, and we pay no fee. `docs/engine-decision-rules.md`
    §3 and ADR-0002 carry the reasoning.

    Two triggers, both detectors of the same event:

    - **A print at our price.** A trade within `tick` of our bid.
    - **An ask fully through our price.** Strictly below, never a touch. An ask
      resting *under* our bid is not a state a book can hold -- the two would
      have matched on contact -- so seeing it in a one-second snapshot is
      evidence that our order was taken while the collector was not looking.

    Both are needed: the tape capture is incomplete, so the book covers what
    the tape missed. A *touch* (`ask == resting`) is deliberately not a fill --
    other bids may sit ahead of ours in the queue, so the promise is fewer
    fills than the market would give, never more.

    `newly_placed` is the exception to the touch rule, and it is the whole
    reason the flag exists. Queue priority is about our own side of the book:
    it decides who gets hit when a seller comes down to us. An order *placed*
    at or above the current ask never joins that queue at all -- it is
    marketable and matches the resting ask on arrival, with nobody ahead of it.
    That is what the leg chase does every time it steps to `min(ask, cap)`, so
    without this branch the chase could never complete a pair. It is still a
    limit order, so it is still booked at our price with no fee (`SPEC.md`).

    Anything unparseable is unknown, not zero, and never fills.
    """
    price = _as_price(resting)
    if price is None:
        return False  # unquotable: there is no order to be filled
    tolerance = tick + 1e-6
    for printed in tape_prices or ():
        traded = _as_price(printed)
        if traded is not None and abs(traded - price) <= tolerance:
            return True
    ask = _as_price(best_ask)
    if ask is None:
        return False
    if newly_placed:
        return ask <= (price + 1e-6)
    return ask <= (price - tick + 1e-6)


def chase_cap(max_pair_cost: Any, entry_price: Any) -> Optional[float]:
    """The highest bid the leg chase may post for the unfilled leg (issue #227).

    A binary pair settles at exactly 1.00, so a completed pair costing more than
    that is a guaranteed loss. `max_pair_cost` is the ceiling on the completed
    pair; one leg is already bought at `entry_price`, so what remains for the
    other is the difference -- floored to whole cents, because the venue quotes
    in cents and rounding up would breach the ceiling by a cent.

    The single ceiling formula, shared by the backtest engine and the live
    trader so the two cannot drift apart. `docs/engine-decision-rules.md` §4 and
    ADR-0003 carry the reasoning.

    A **negative** result is meaningful, not an error: the filled leg already
    cost more than the whole pair may, so there is nothing left to spend. Both
    engines express that through `min(ask, cap)`, which then sits below any
    quotable price and raises nothing -- no separate branch needed.

    Anything unparseable is unknown, never zero: zero reads as "chase to the
    floor", which is a decision, and we have not made one.
    """
    cap = _as_price(max_pair_cost)
    entry = _as_price(entry_price)
    if cap is None or entry is None:
        return None
    # `+ 1e-9` before the floor: 0.51 * 100 lands on 50.999999... in binary, and
    # without the nudge a legitimate 0.51 floors to 0.50.
    return round(math.floor((cap - entry + 1e-9) * 100.0) / 100.0, 2)


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
