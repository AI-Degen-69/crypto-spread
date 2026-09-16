# Plan — Issue #225: the entry anchor

**Size**: Standard. **Type**: Code. **Stack**: Python 3.12, pytest.

Spec: `SPEC.md`. Gates: `CONSTRAINTS.md`. Rule text: `docs/engine-decision-rules.md` rule 1.

## Locked interface

No new public surface. One new local in `_simulate_window`:

```python
orders_live: bool   # a quote reached fill detection on an earlier tick and was not cancelled
```

The anchor condition, mirroring `live_trader.py:4456`:

```python
if (not filled_up and not filled_down and delay_expired
        and (entry_cancelled or not orders_live)
        and anchor_mid is not None):
```

## Tasks

### T1 — backtest: reprice until placed, anchor on the two-sided mid `[Backend/Logic]`

Files: `backtest/engine.py`.

1. Replace the `if resting_up is None` lazy anchor with the condition above.
2. `anchor_mid = _two_sided_mid(ub, db)`; delete the `s["mid"]` preference.
3. `no_book_hold = (not orders_live) and anchor_mid is None`, folded into `quotable`. A quote
   already resting is unaffected — it is already on the book.
4. Set `orders_live` where `window_entered` is set: the quote reached the book on that tick.
5. The re-entry re-quote anchors on `reentry_mid`, the same two-sided mid its own test just
   passed, instead of `s["mid"]`.

### T2 — correct the fixtures that describe impossible books `[Test]`

Files: `tests/test_backtest_engine.py`, `tests/test_entry_timeout.py`,
`tests/test_osc_dash_integration.py`.

`snap()`, `_make_snap()`, `_make_fake_tick()` and `_chaseable_window()` each centred the DOWN
leg on an unrelated ask, so their two-sided mid was not the mid they claimed. Pin DOWN at the
complement. `_make_snap` gains an explicit `down_bid` opt-out for the two tests whose subject is
a leg-imbalanced book.

### T3 — rewrite the tests whose subject was the removed behaviour `[Test]`

Files: `tests/test_backtest_engine.py`, `tests/test_entry_timeout.py`.

1. `test_anchor_uses_mid_only_prefix` asserted the defect. It becomes
   `test_the_anchor_ignores_a_one_sided_mid_prefix`, with a control proving the abstention is
   the anchor source and not another gate.
2. `test_backtest_one_sided_open_book_does_not_cancel_entry` keeps its real subject — no
   *latched* cancel — and no longer expects a fill on the unpriceable tick.
3. The three settlement stages that are no longer reachable end to end move to direct
   `resolve_naked_settlement` calls, following the precedent already in the file.

### T4 — parity scenario `[Test]`

File: `tests/test_entry_anchor_parity.py` (new).

Drive both engines over one snapshot sequence that prices one leg on tick 0 and both on tick 1.
Assert neither quotes tick 0, and that the backtest fills at the live engine's own resting
price. A third test drives the old anchor's price through the same window and asserts it does
not fill, so the parity assertion cannot pass on a backtest that simply never fills.

## Sequencing

T1 and T2 land together — the tree is red between them. T3 follows. T4 last.

## Risks

- **Re-baselining instead of explaining.** Mitigated by `CONSTRAINTS.md` §2 and by every moved
  expectation carrying its reason.
- **The parity test pinning today's tuning.** Mitigated by §4: the backtest side reads the live
  engine's resting price rather than a literal.
