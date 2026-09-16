# Engine decision rules

The live engine (`strategy/live_trader.py`) and the backtest (`backtest/engine.py`) run the
same strategy twice. **Their parameter *values* are deliberately independent** — the operator
runs live on one setting while sweeping freely in research, and that must stay true. What
must be identical is the *decision logic*: the same trigger conditions and the same action on
trigger, so a backtest result predicts live behaviour.

This file is the agreed definition of each rule. One entry per rule: a name, what it does,
when it fires, and what happens when it fires. Issue #214 builds the parity tests that hold
both engines to it.

Status legend: **agreed** = defined here and enforced by a parity test.
**pending** = not yet defined.

---

## Invariant 0 — no invented numbers  *(agreed 2026-09-16)*

**No number that did not come from the book enters a decision. If a value is missing, the
engine does not act.**

It does not substitute a default, does not guess from a name, does not derive a plausible
stand-in, and does not carry a stale value forward as if it were current. It waits and
re-evaluates on the next tick. Abstaining is always available and always cheap; acting on a
fabricated number is a real position taken on information that does not exist.

This is not a style preference. Every fabrication found so far produced the same failure: the
invented value was *plausible*, so nothing looked wrong, and the gate meant to catch the
problem was handed the one input that made it pass. A missing value that halts the engine is
visible on the first tick. A missing value replaced by `0.50` is invisible for months.

Known instances and their status:

| fabrication | where | status |
|---|---|---|
| `0.50` for an unpriceable leg | live `two_sided_mid_with_default` | fixed, #207 |
| `0.50` for the entry anchor | live entry quoting | fixed, #206 |
| `0.40` for the stop exit price | `live_trader.py:1735` | removed, rule 2 |
| `0.50` for the re-entry mid | `live_trader.py:4744` | **open** |
| window length guessed from the slug (`"15m" -> 900s`) | `live_trader.py:4483`, `:2628` | **open** |
| elapsed seconds taken from the snapshot index | `backtest/engine.py:769` | **open** |

---

## Parameter classes  *(agreed 2026-09-16)*

Knobs fall into two groups, and the UI must keep them apart:

- **Tuning knobs** — what the operator sweeps and tunes: offset, stop loss, delay, share size.
- **Structural limits** — bounds on what the engine may do at all. Changeable, but not part of
  the tuning set and not swept casually: `max_pair_cost` (rule 4) and `quote_range` (rule 6).

A structural limit exists to make a whole class of action impossible, so mixing it into the
tuning group invites it being swept to a value that disables it — which is exactly how the
backtest's pair-cost default ended up at 1.05.

---

## Invariant 1 — the window clock  *(agreed 2026-09-16)*

Every time gate in the strategy — entry timeout, late-start skip, entry delay, naked-leg
timeout, re-entry remaining time — is a fraction or offset of the window. They are only as
sound as the clock they read, so the clock has exactly one definition:

```
window_length  = end_ts - start_ts        # from the market's own metadata
elapsed        = now - start_ts           # live
elapsed        = snapshot_ts - start_ts   # backtest
```

**If `start_ts` or `end_ts` is missing or not a sane pair, the window has no clock and no time
gate may be evaluated. The window is not traded.** This is Invariant 0 applied to time.

Two fabrications removed by this rule:

1. **Window length guessed from the market name.** `(900.0 if "15m" in slug else 300.0)`
   (`strategy/live_trader.py:4483` and `:2628`) reads the duration out of a substring of the
   slug. Any name that does not happen to contain `15m` silently becomes a 5-minute window,
   and every time gate in that window is then measured against a length nobody verified.
2. **Elapsed time taken from the snapshot index.** `elapsed = float(s_idx)`
   (`backtest/engine.py:769`) counts snapshots as if each were exactly one second. When the
   collector misses seconds — which it does, this is the same gap that makes the tape
   incomplete in rule 3 — every time gate in that window is measured against a made-up clock,
   and nothing reports it.

Operator decision, 2026-09-16.

---

## 1. `entry_anchor` — the opening quote price  *(agreed 2026-09-16)*

**What it does.** Decides the price of the two opening buy orders for a window.

**Trigger.** The window is open, no order exists yet and neither leg has filled, **and** the
entry delay has expired (`elapsed >= entry_delay_sec`; with a delay of 0, the first tick).

**Action on trigger.**

```
resting_up   = clamp(round(mid - offset, 3), 0.01, 0.99)
resting_down = clamp(round((1 - mid) - offset, 3), 0.01, 0.99)
```

**Two points settled explicitly:**

1. **The price is recomputed on every tick until an order actually exists**, so the submitted
   price is the mid at placement time, not a mid carried over from an earlier tick. It latches
   only once an order is live. This is the live engine's behaviour
   (`strategy/live_trader.py:4396-4405`); the backtest anchored once at delay expiry and never
   re-anchored (`backtest/engine.py:779-790`), and is aligned to the live rule.

2. **`mid` means the two-sided mid only.** If either side of the book cannot be priced, there
   is no anchor and **no quote is placed** — the window waits. The live engine already does
   this (issue #207); the backtest preferred the recorded one-sided `s["mid"]` and quoted
   anyway, and is aligned to the live rule.

**Consequence to expect:** aligning the backtest changes historical sweep numbers. That is
the point — the old numbers described entries the live engine would not have placed.

---

## 2. `stop_loss` — cutting a single filled leg  *(agreed 2026-09-16)*

**What it does.** Protects one leg that filled while the other did not, by exiting it once
the market has moved against the price it actually entered at.

**Arming.** The stop is armed the moment an entry leg fills, at

```
stop_price = clamp(round(entry_fill_price - stop_threshold, 2), 0.01, 0.99)
```

Worked example: mid 0.49, offset 0.03 -> entry rests at 0.46; with a 0.05 stop the stop
price is 0.41.

**The stop is held ready, not rested on the book.** This is a venue constraint, not a
preference. The prediction-market CLOB accepts `GTC`, `GTD`, `FAK` and `FOK` only — there is
no stop or trigger order type (stop-loss triggers exist on Polymarket *Perps*, a different
product). A SELL LIMIT at 0.41 while the bid is 0.45 means "sell at 0.41 or better" and
executes immediately at 0.45, which exits the leg the instant it fills instead of protecting
it. `postOnly` would only make the venue reject it. So the stop is held pre-signed in memory
(`STAGED`) and submitted the moment the trigger fires — same outcome, zero latency, no
accidental instant exit. (Confirmed against docs.polymarket.com/trading/place-orders,
2026-09-16; this re-confirms the conclusion of issue #87.)

**Trigger.** Measured from the leg's own entry price, never from a fixed 0.50 (issue #209):

```
excursion = round(entry_price - mid, 6)              # filled UP leg
excursion = round(mid - (1 - entry_price), 6)        # filled DOWN leg
```

The stop fires when the **maximum excursion seen** reaches the threshold and no reversal has
been latched. Excursions round to 6dp because the raw subtraction
(`0.45 - 0.40 == 0.04999999999999999`) would sit a hair under an exactly equal threshold and
silently skip the stop.

**Action on trigger (OCO).**

1. Cancel the opposite leg's resting buy order.
2. Sell the filled leg at the best executable bid.

**And the other direction:** if the second leg fills before the stop triggers, the stop is
cancelled and the pair is merged. Exactly one of the two outcomes happens.

**No executable bid = no exit.** The exit price is resolved through the existing
`_resolve_exit_bid` ladder (issue #160): direct book bid, then binary complement ask, then
the latched valid bid, then the latched complement, then the synthetic mid. If every stage
fails there is no exit — the position is held and the trigger is evaluated again on the next
tick. **No fabricated price is ever used.** The stop-exit path previously fell back to a
hardcoded `0.40` (`strategy/live_trader.py:1735`), which is the same defect class as the
fabricated `0.50` fixed in #207 and #206; it is removed as part of this rule.

**Fees.** A stop exit crosses the book, so it is a taker and pays the venue taker fee. The
backtest charges it; the live engine computes no fees at all (tracked separately).
## 3. `fill_rule` — when a resting quote fills  *(agreed 2026-09-16)*

**What it does.** Decides whether a resting buy order is considered filled.

**Trigger.** Either of these, whichever happens first:

1. **A real trade prints at our price** — a tape print within the tick tolerance of our
   resting price.
2. **The book price passes fully through ours** — the best ask is *below* our resting price,
   not merely equal to it.

**Why both.** The tape capture is not complete: the collector does not observe every trade
within every second, so a rule that trusts the tape alone misses fills that really happened.
The book check covers what the tape missed.

**Why "fully through" and not "touch".** At an equal price there may be a queue of other bids
ahead of ours, so a touch does not mean we traded. Only once the price passes through is the
fill effectively certain. This is deliberately more conservative than reality: the backtest
should promise fewer fills than the market would give, never more.

**Action on trigger — our resting price, and no fee. Always.**

Both triggers are *detectors*. They answer "did our order get taken", not "what did we pay".
The entry quote is a limit order that sits and waits, so whenever it fills, somebody came to
us. We were the maker. We get our own price and we pay no fee.

| how we saw it | who crossed | fill price | fee |
|---|---|---|---|
| a trade printed at our price | the seller came to us | **our resting price** | none |
| the ask passed below our price | the seller came to us; the print was missed | **our resting price** | none |

**Why the second row is not a taker fill.** A resting ask *below* our resting bid is not a
state a book can hold — the two would have matched the instant they met. Seeing it in a
one-second snapshot is evidence that our order was taken between the two snapshots, not
evidence that we crossed into anything. Whoever sold to us was the aggressor, and an aggressor
pays the fee, not us. Operator correction, 2026-09-16, replacing an earlier draft of this rule
that booked the ask price and charged a taker fee on the second row.

**Where a taker fee does belong: the exits.** A stop or a naked-leg timeout sells into the
best bid. That crosses the book, so it is a taker and it pays the fee (§2). Entries never do.

**The one marketable-limit case.** The leg chase (§12) raises the unfilled leg to
`min(ask, max_affordable)`, which can land exactly *on* the ask, and an order placed at the ask
is matched on arrival. It is still a limit order, so it is booked here as a maker fill at our
price with no fee — the operator's rule, knowingly, for the narrow case where it understates
cost by one fee. A quote placed at the ask fills at the ask, and our price *is* the ask, so
only the fee is at stake.

The entry price latches on the tick it fills and never moves afterwards — a later chase step
may change the resting quote but must not rewrite the entry that the P&L and the stop are
measured against.

**No model switch.** This is the single fill rule. `fill_model` is removed as a knob: it is
not an operator choice, it is a statement about how the venue behaves, and the previous
default (`tape` alone) described only half of what the live engine does. Operator decision,
2026-09-16.

**Consequence for the live engine:** it fills on a touch (`ask <= resting`) today and moves to
the fully-through rule, so it will fill slightly less often than before. Accepted knowingly.

**Consequence for issue #205:** the 16x gap measured there between `tape` (3.6% of windows)
and `cross` (57%) is explained by this — the shipped preset ran the tape branch alone while
the live engine also fills from the book.
## 4. `max_pair_cost` — the ceiling on a completed pair  *(agreed 2026-09-16)*

**What it does.** Stops the leg chase from completing a pair that costs more than the pair can
ever pay out. A binary pair settles at exactly 1.00, so paying more than 1.00 for both legs is
a guaranteed loss.

**Where it applies: the chase, and only the chase.** This is the one place the risk exists.
An opening pair cannot breach the cap — both legs are quoted at `mid - offset` and
`(1 - mid) - offset`, so their cost is always `1 - 2*offset` regardless of where the market
is. The chase is different: it raises one leg toward the ask to complete a pair, and without a
ceiling it can raise it past the point where the pair is worth completing.

**Trigger.** One leg is filled, the other is not, and the chase wants to raise the unfilled
leg's quote.

**Action.** The chased leg's price is capped at

```
max_bid = floor((max_pair_cost - entry_price_of_filled_leg) * 100) / 100
```

floored to whole cents, so `entry + chased` can never exceed the cap through rounding. The
chase raises the quote only, never lowers it.

**Default 0.99. Hard maximum 1.00.** Above 1.00 the knob is meaningless — it would authorise
paying more for the pair than the pair returns. Both engines clamp to 1.00.

**Two things removed by this rule:**

1. **The backtest's entry-side block.** It tested our own resting pair cost, which is the
   constant `1 - 2*offset`, so it either passed every window or blocked every window
   depending only on the offset — an on/off switch wearing a market gate's clothes. It also
   skipped fill detection when it failed. Deleted; the offset already controls entry cost
   directly and legibly.
2. **The declared range divergence.** The backtest's default of 1.05 existed only to disable
   that entry block by sweeping above 1.00 (documented at `backtest/engine.py:352-360`). With
   the block gone there is nothing to disable, so the two engines share one range and one
   default, and the exception disappears.

**Not a market gate.** Checking the book's two asks at entry was considered and rejected: the
two asks of a binary pair always sum to roughly 1.00-1.01, so the check carries no
information. Operator decision, 2026-09-16.

**Naming.** The backtest field `pair_cost_gate` is renamed to `max_pair_cost`, matching live.
The old name described the deleted entry gate, not the surviving cap.
## 5. `unpriceable_leg_skip` — a book that cannot be priced  *(agreed 2026-09-16)*

An instance of **Invariant 0**.

**Trigger.** Either leg cannot be priced — no bid, no ask, or neither.

**Action.** No quote is placed on **either** leg. The window waits and is re-evaluated on the
next tick. Nothing is latched: this is a transient condition, not a skip decision.

**Never** substitute a value for the unpriceable leg. Reporting a fabricated `0.50` tells the
adverse-open gate the market is perfectly balanced when in truth nothing was priced at all —
the gate exists to catch exactly that, and the substitution is what blinds it (issue #207).
## 6. `quote_range` — which prices may be quoted at all  *(agreed 2026-09-16)*

**Replaces both `entry_band` (issue #213) and `adverse_open` (issue #208), which are deleted.**

**Why they are deleted.** Both computed `|mid - 0.50|` and latched the window shut — the same
gate under two names, two thresholds and two evaluation times. Distance from 0.50 measures
nothing this strategy depends on: we quote around the *current* mid (rule 1), not around 0.50.
A market at 0.70 offers exactly the trade a market at 0.50 offers — both legs bought for
`1 - 2*offset`. The number they guarded on stopped being load-bearing when the entry anchor
was fixed.

**Movement was considered and rejected.** A gate on "how far the mid has moved since the
window opened" was proposed and turned down: a window can open at 0.50, fall to 0.20, and
still be perfectly quotable around 0.20, because the volatility that makes the strategy work
is still there. Movement is not the risk. Running out of window time is, and that is its own
rule.

**The rule.** There is a quotable price range. Inside it, quote. Outside it, do not.

```
quote_range = (0.10, 0.90)        # default
```

**Trigger.** The mid falls outside `quote_range`.

**Action.** No quote is placed. Nothing is latched — the range is re-checked on every tick, so
a market that leaves the range and comes back is quotable again, provided enough window time
remains (see the window-time rules).

**Why a range and not a band.** Past roughly 0.90 the market is decided: the cheap leg will
never fill, so resting it is a position on one side only. The range says exactly that, in the
units the operator actually thinks in, and it says nothing about 0.50.

**Measured on the mid.** The resting quotes themselves may sit slightly outside the range —
at a mid of 0.90 with a 0.03 offset the cheap leg rests at 0.07 — which is fine. The range is
a judgement about the market, not about the order prices.

**No permanent latch.** Both deleted gates cancelled the window for good on a single failed
check. That latch is what issue #212 works around, by building a special re-entry path for
one of the two. With no latch there is nothing to resurrect.

Operator decision, 2026-09-16.

---

## 7. *(merged into rule 6)*
## 8. `dead_zone` — the end of the window is not tradeable  *(agreed 2026-09-16)*

**What it does.** Defines the tail of the window as untradeable and keeps the engine out of it.

**The operator's reading:** in the last 30 seconds of a 5-minute window the prices are already
decided and there is no room left; the last tenth of a 15-minute window is the same — the
price is no longer moving, it is drifting to settlement. Dead space. Nothing to do there.

**Trigger.** The remaining window time falls below the dead-zone threshold.

**Action.** No entry. A window whose first observed tick already lands inside the dead zone is
not entered at all — which is what the old `late_start_skip` was reaching for, from the other
end of the clock.

**Expressed as time remaining, not time elapsed.** These are not the same rule. "10% has
elapsed" and "10% remains" are different boundaries, and only the second one describes the
thing being avoided. The old gates measured from the window's start, so the same setting meant
a different thing at every window length.

**Default: 10% of the window remaining.** A structural limit (see Parameter classes), not a
tuning knob.

**Switchable unit.** The knob carries a mode — percent of window, or absolute seconds — and a
value. Percent at 0.10 is the default.

**Which unit is actually right is an open question, deliberately.** The argument for seconds is
that "enough time for two legs to fill and merge" is a fixed quantity, and a 15-minute window
does not make filling three times slower. The argument for percent is that the dead tail
scales with the window. This has never been measured. The switch exists so it can be measured
rather than argued; the default stands until data replaces it.
## 9. `entry_timeout` — **deleted**  *(agreed 2026-09-16)*

**The rule is removed from both engines.** A quote that has not filled is cancelled only by the
dead zone (rule 8), not by a timer of its own.

**What it was.** A giving-up clock: if nobody had filled our resting quotes after some fraction
of the window, cancel them and sit the window out. The backtest ran it at 10% of the window,
so a 5-minute window was abandoned after 30 seconds with four and a half minutes of
opportunity left.

**Why it goes.** It was a crude proxy for a danger that is now covered directly. The fear was
being left with one leg and no time to pair it — which the stop (rule 2), the naked-leg cut
(rule 11) and the dead zone (rule 8) each address at the point where it actually happens.

**And the premise behind the fear does not hold.** A resting quote is not ambushed by a market
that has run away from it. The mid does not jump from 0.50 to 0.60; it travels through 0.53,
and at 0.53 the DOWN leg is worth exactly our 0.47 bid and is taken there, at fair value, at
the moment of crossing. Either the market comes to the quote — which is the fill the strategy
exists to collect — or it stays away and nothing happens. There is no third case in which a
stale quote sits and then fills badly. Operator's correction, and it is the right one.
## 10. `naked_leg_stop` — **merged into rule 2**  *(agreed 2026-09-16)*

**There is one stop threshold, not two.** `exit_thresh_naked` is deleted from both engines.

**What it was.** A second, tighter stop that applied only to an unpaired leg, on the reasoning
that a naked leg bleeds more than a paired position.

**Why it goes.** A completed pair cannot lose. Both legs bought for `1 - 2*offset` settle at
exactly 1.00 — the profit is locked the moment the pair completes and no subsequent price move
can touch it. There is nothing for a paired stop to protect.

The code already says so: every exit site in the backtest requires exactly one leg filled, so
the "paired" threshold never governs a genuinely paired position. Two names, two knobs, two
numbers, and only one of them was ever alive.

The single `stop_loss` of rule 2 is that live one.
## 11. `naked_leg_timeout` — **merged into rule 8**  *(agreed 2026-09-16)*

**An unpaired leg is cut in the dead zone.** `naked_leg_timeout_pct` is deleted as a separate
knob; the dead zone is the deadline.

**What it was.** A clock measured forward from the moment the leg went naked:
`naked_elapsed >= naked_leg_timeout_pct * window_length`. At the shipped 0.70 it produced a
different exit for the same setting depending only on when the fill happened — a leg filled at
the 10% mark was cut at 80%, while a leg filled at the 50% mark was cut at 120%, which is after
settlement, so the timeout never fired at all (issue #211).

**Why the dead zone replaces it.** Issue #211 asks for exactly the dead zone's boundary in
exactly its units: *"cut an unpaired leg when under 10% of the window is left (30s on a 5m, 90s
on a 15m)"*. That is rule 8's threshold. Rather than two knobs that must be kept consistent,
the dead zone is the single statement about the end of the window:

> **In the dead zone: open nothing, and close what is open.**

This closes #211.
## 12. `leg_chase` — chasing the second leg after one fills  *(agreed 2026-09-16)*

**What it does.** One leg filled, the other did not. The chase raises the unfilled leg's bid
toward its ask to complete the pair, rather than carrying a naked leg.

**The escalation ladder.** Three outcomes, best first, and the engine works down them:

1. the other leg fills at our original price — the pair costs `1 - 2*offset`, full profit;
2. the chase completes the pair below `max_pair_cost` — a smaller profit, but a profit;
3. no pair — the stop takes the loss (rule 2).

**Willingness to pay grows with elapsed time, not with the first tick.** Today the chase fires
on the tick immediately after the one-sided fill and on every tick after, with no condition on
the market having moved (issue #210). With the mid still at 0.50 and our UP filled at 0.47
because someone sold cheap for a second, the chase immediately lifts DOWN from 0.47 to the ask
at 0.51 — paying 0.98 for a pair worth 1.00 while nothing whatsoever has changed in the market.
The patience that would have collected the same fill at 0.47 costs nothing to exercise.

**The rule.** The chase ceiling walks from our original quote up to the cap as the window runs
out:

```
max_affordable = floor((max_pair_cost - entry_price_of_filled_leg) * 100) / 100
progress       = clamp((now - went_naked_at) / (dead_zone_start - went_naked_at), 0, 1)
ceiling        = original_resting + progress * (max_affordable - original_resting)
target         = min(other_leg_ask, ceiling)
```

The quote is only ever raised, never lowered. At `progress = 0` the chase offers nothing beyond
the original quote; at the dead zone the full cap is available, which is the last chance to
complete a pair before rule 8 cuts the leg.

**Why the endpoint is the dead zone and not a horizon measured from the fill.** A window that
goes naked at the 10% mark and one that goes naked at the 80% mark are both walking toward the
same fixed moment, so the same setting means the same thing in both. Measuring forward from the
fill is the defect issue #211 documents in the naked-leg timeout, and it is not repeated here.

**No new knob.** The ladder is defined entirely by `max_pair_cost` (rule 4) and the dead zone
(rule 8), both already agreed.

**The chase and the stop can never both be available.** The chase needs
`other_ask <= max_pair_cost - entry`; the stop needs the mid at `entry - stop_loss`. Overlap
would require `max_pair_cost >= 1 + stop_loss`, and the cap is hard-limited to 1.00, so it
cannot happen. As the market moves against the filled leg the other leg becomes more expensive
by the same amount, so salvage prices itself out exactly as it becomes necessary. The ladder
above is therefore enforced by arithmetic, not by a priority flag: the chase window always
closes before the stop window opens, with a band in between where the engine simply waits.

This closes #210.
## 13. `fresh_start` — the engine has no memory inside a window  *(agreed 2026-09-16)*

**The rule.** Whenever the market is clean, the engine evaluates the window exactly as if it
had just opened. There is no record of what happened earlier in it and no special path back in.

**Clean** means no open position **and** no resting orders. That is true in four situations,
and the engine does not distinguish between them:

- nothing has been tried yet;
- a pair merged successfully;
- an unpaired leg was stopped out;
- the orders were cancelled.

**Trigger.** The market is clean, and all three standing conditions hold: the mid is inside
`quote_range` (rule 6), the window has not reached the dead zone (rule 8), and both legs are
priceable (rule 5).

**Action.** Quote, exactly as rule 1 describes for a window that has just opened.

**What this replaces.** Five knobs and two mechanisms:

| removed | why |
|---|---|
| `reentry_drift_band` | measures distance from 0.50, which no longer enters any decision |
| `min_requote_remaining_sec` | the dead zone is the time gate |
| `reentry_min_remaining_pct` | the same gate again in a second unit |
| `max_reentries_per_window` | nothing to cap — the standing conditions are the limit |
| the post-merge requote path | a special case of this rule |
| the drift-skip re-entry path | existed only to undo a latch that rule 6 removed |

**And it fixes a live/backtest divergence nobody had recorded.** The live engine already opens
a fresh round after a merge. The backtest ends the window at the merge, and again at a stop —
both are a `break` out of the tick loop. A 15-minute window in which the live engine completed
three pairs is scored by the backtest as one. The backtest has been systematically
under-reporting profit per window, and the reason it went unnoticed is exactly the reason this
whole document exists.

This closes #212.
## 14. `naked_leg_at_expiry` — what happens to a leg that never paired  *(agreed 2026-09-16)*

**The switch.** Two values, and it replaces `stop_loss_enabled`:

- **`close`** *(default)* — the dead zone closes the unpaired leg at the book.
- **`hold`** — the leg is carried to settlement and pays 1.00 or 0.00.

**Why the old name goes.** `stop_loss_enabled = False` reads as "the stop is off", but the stop
is still armed and still fires on an adverse move. What the flag actually changed was the end
of the leg's life, and the new name says so.

**Why this is a switch and not a rule.** Both behaviours are defensible, and which is better is
a risk preference, not a fact about the engine. It is also the one place in this document where
rule 8's "close what is open" has a declared exception, so the exception is named rather than
implied.

**The arithmetic, stated honestly.** A leg trading at 0.25 means the market prices a 25% chance
of settling at 1.00. Across 100 such legs, 75 expire worthless and 25 pay 1.00 — which is 25
units over 100 legs, or 0.25 each. The frequent outcome and the expected value are different
questions, and "it usually goes to zero" is already inside the price. On those numbers holding
is worth about a cent more than selling, because selling pays the spread and the taker fee and
holding does not.

**Two reasons the arithmetic may not hold, and they pull opposite ways:**

1. **Favourite-longshot bias.** Prediction markets are documented to overprice longshots. If a
   leg priced at 0.25 wins only 20% of the time, holding loses and closing is correct.
2. **Liquidity dies near settlement.** The bid in the dead zone may be well below the last mid,
   so closing realises less than the arithmetic above assumes.

**The default is `close`, deliberately.** The edge for holding is about a cent; the cost is
turning every unpaired leg into a coin flip for its full size. A cent of expected value does
not pay for that variance. The measurement that could overturn this is filed separately —
until it lands, neither reading is treated as established.

Operator decision, 2026-09-16.
