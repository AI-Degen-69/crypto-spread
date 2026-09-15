# SPEC.md — Issue #191: Backtest books 0 for naked legs it cannot mark

## 1. Goal
Give `backtest/engine.py` the same rollover-settlement discipline the live engine
already has. A naked leg carried to window close must be worth something — a
resolved mark, or its redemption value — never a silent `0.00c` chosen because
the book happened to be empty at the final poll.

## 2. Background & Evidence

`_simulate_window` marks a still-open naked leg from the last snapshot's
`best_bid` (`backtest/engine.py:1023-1035`). Both branches are guarded by
`is not None`. A losing contract loses its bid side before expiry — nobody bids
on something settling at zero — so the guard fails, neither branch runs, and the
window contributes `0.00c` instead of the loss of the stake. Winners keep a bid
near 1.00 and are always booked. The error is one-directional by construction
and does not average out with more data.

`research/sweeps/audit_settlement.py` over the 3-day capture (2026-09-13 →
2026-09-15):

```
naked-settle windows: 351
engine mark: wins=169 losses=1 none-marked(0 pnl)=181
true settlement: wins=169 losses=182
bias (true - engine) total: -395.56$ at size 5
unmarked by won/lost: Counter({'lost': 181})
```

181 unmarked windows, **181 of them losers — 100%**.

**Live already solved this.** Issue #160 shipped
`LiveTrader._resolve_exit_bid` (`strategy/live_trader.py:5386-5438`), a
five-stage ladder that raises `RuntimeError` rather than assume a price:

1. direct book bid on the held leg
2. binary complement ask: `1.0 - opposite_ask`
3. latched last-valid book bid on the held leg
4. latched binary complement ask
5. synthetic leg mid, only when a book update was observed and the mid is not
   exactly 0.50

The backtest never received the same treatment. It still stops at stage 1 and
books zero on failure. That is the whole defect: **a parity gap against the
reference implementation**, not a missing feature.

Worked example — `sol-up-or-down-5m`, 2026-09-13, 204 samples over 299s, held
DOWN leg resting at 0.470, final ticks `DOWN: bid None ask 0.01` /
`UP: bid 0.99 ask None`, final mid 0.995:

| Path | Mark | Booked |
|---|---|---|
| engine today | none (`best_bid is None`) | **0.00c** |
| live's ladder | stage 3, latched DOWN bid 0.04 | **-43.00c** |
| true redemption | DOWN settles 0.00 | **-47.00c** |

## 3. In Scope

### 3.1 Port the live resolution ladder into the engine
Add a resolver to `backtest/engine.py` that mirrors `_resolve_exit_bid` stages
1-4 against `window_snaps`: final held-leg bid, `1.0 - final opposite ask`, last
valid held-leg bid seen anywhere in the window, `1.0 - last valid opposite ask`.
Stage 5 (synthetic mid) is replaced by 3.2, because a replay knows the outcome
and a live engine at rollover does not.

### 3.2 Redeem when the ladder cannot resolve
Where no stage yields a mark, resolve the held side's outcome from the last
observed mid and book redemption directly: mid above 0.5 means the held side
settles at 1.00, otherwise 0.00; P&L is `(settlement - resting) * 100`. This is
the rule `audit_settlement.py` already applies, so the audit and the engine
cannot diverge. **No taker fee on a redemption** — there is no closing trade.

### 3.3 Abstain instead of guessing
When there is no usable reference mid at all, or the reference mid is exactly
0.50, the window keeps `0.00c` and is not flagged as settled. Live raises here;
a replay over historical ticks cannot usefully raise, so it abstains and makes
the abstention countable.

### 3.4 Make the outcome observable
Add `settled_unmarked: bool` and a `settle_source: str` (`direct_bid`,
`complement_ask`, `latched_bid`, `latched_complement_ask`, `redeemed`,
`unresolved`) to `WindowResult`, and surface both in the per-window export at
`backtest/engine.py:1136`. A sweep must be able to separate "closed at a real
quote" from "redeemed" from "abstained" without re-deriving it.

### 3.5 One definition of the rule
The last-mid/redemption arithmetic already exists twice, inline and
near-identically, in `research/sweeps/ev_lab.py` and `research/sweeps/sim2.py`.
A third inline copy is how issue #182 happened. The rule lands in one exported
function in `backtest/engine.py`; `ev_lab` and `sim2` import it and keep
returning `settle_won` / `settle_delta` for `summarize` unchanged.

## 4. Out of Scope
- `strategy/live_trader.py`. Live is the reference implementation; the backtest
  moves toward it, never the reverse. Its ladder is not modified, re-tuned, or
  refactored by this issue.
- `ev_lab.summarize`'s `settle_correct` path. It already applies the correction
  by default and `research/sweeps/phase5_band.py` passes it explicitly, so the
  sweep headline numbers are already settlement-corrected. Touching it would
  double-count.
- Regenerating any `research/sweeps/*.json`. The stale marker stays.
- `scripts/collect_ticks.py` — a continuous capture is running.
- Venue logic, order routing, the tick schema, and the dashboard.

## 5. Acceptance Criteria
1. A window where one leg fills and the held side's final `best_bid` is `None`
   books the resolved loss, not `0.00c`, proved by a test that fails on master.
2. A mirrored test proves a winning unmarked leg books the full win.
3. A window whose held leg has a real final `best_bid` produces byte-identical
   `pnl_cents`, `fees_cents`, `exit_price` and `settlement_mid` to master.
   Pair-captured and stopped-out windows are likewise unchanged.
4. Each ladder stage has a test that forces exactly that stage and asserts the
   `settle_source` it reports, including `redeemed` and `unresolved`.
5. `research/sweeps/audit_settlement.py` keeps printing the raw uncorrected
   bias — it is the evidence in #191 — and adds a corrected line measured
   through the shared resolver that lands at approximately 0.
6. `python -m pytest -q` green against the 856-test baseline.
7. `research/sweeps/RESULTS-ARE-STALE.md` records that pre-fix
   `scripts/backtest.py` output for hold-to-settle configs is invalid, and that
   the sweep JSONs were already corrected by `summarize`.

## 6. Edge Cases
- **Both legs filled (pair captured).** Untouched. The resolver is only reached
  when exactly one leg filled and no exit fired.
- **Opposite ask is 0.0 or above 1.0.** Rejected, exactly as live rejects it;
  the ladder falls through to the next stage rather than synthesizing a mark of
  1.00 or a negative one.
- **Held leg has a bid of 0.0.** Live treats `0.0 < bid <= 1.0` as the validity
  window, so a literal `0.0` bid is not a mark — it falls through. The engine
  must match, or a zero-bid window books the same silent zero under a new name.
- **Final mid exactly 0.50.** Ambiguous. Abstain, book `0.00c`, report
  `unresolved`.
- **Window with no snapshots, or no two-sided mid on either book.** No reference
  mid exists; abstain rather than defaulting to 0.50.
- **Latched values from early in the window.** The latched stages scan backward
  for the last *valid* quote, which may be many ticks old. That is accepted —
  live accepts it too — but it is why `settle_source` must be recorded, so a
  stale mark is visible in the results rather than indistinguishable from a
  fresh one.
- **Redemption and fees.** The 0.50-mark naked fee already charged on entry
  stays. No second fee is charged on a redemption.

## 7. Deferred Proposal — reconcile the fee model for marked settlements
Stages 1-4 produce a mark and the engine charges a taker fee on it, modelling a
sale. Stage 3.2 produces a redemption and charges nothing. For a hold-to-settle
config (`ex=none`) the leg is never actually sold, so charging a close fee on a
resolved mark arguably overstates cost by up to 7 bps of the mark. Deciding this
properly means separating "the strategy closes at rollover" from "the strategy
redeems", which is a `BacktestParams` semantics change and its own issue. This
issue keeps today's fee behaviour on the marked path so the change stays bounded
to the silent zero.
