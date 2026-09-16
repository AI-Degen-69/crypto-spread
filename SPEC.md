# SPEC.md — Issue #206: Live entry quotes must anchor to the live mid

Binding while `fix/live-entry-anchor-mid-206` is live. Supersedes the #193 contents.

## 1. Goal

The opening (round-0) resting quotes in `LiveTraderEngine._update_market_strategy`
must be priced off the market's current mid at the moment they are placed, the
way the comment above them already claims and the way the backtest
(`backtest/engine.py:786-788`) and the re-quote path
(`strategy/live_trader.py:5190`) already do.

Today they are priced off a hardcoded `0.50` on every window, because the
`locals()` guard at `strategy/live_trader.py:4384-4385` reads names
(`up_mid`, `down_mid`) that are never bound in that scope.

## 2. Current behaviour (the defect)

```python
u_m = up_mid if 'up_mid' in locals() and up_mid is not None else 0.50
d_m = down_mid if 'down_mid' in locals() and down_mid is not None else 0.50
resting_up = round(min(0.99, max(0.01, u_m - self.offset)), 3)
resting_down = round(min(0.99, max(0.01, d_m - self.offset)), 3)
```

`'up_mid' in locals()` is always `False`. Every opening quote is
`0.50 - offset` on *both* legs, in live and paper alike.

`mstate.mid` — the synthetic two-sided mid, computed 20 lines earlier at
`:4365` — is exactly the value the comment describes and is simply unused here.

## 3. Required behaviour

Let `mid` be `mstate.mid` (the value already used by the re-quote path and by
every gate downstream of `:4438`):

```
resting_up   = clamp(mid - offset)
resting_down = clamp((1.0 - mid) - offset)
```

with `clamp(x) = round(min(0.99, max(0.01, x)), 3)`, identical to the existing
re-quote and backtest formulas.

### 3.1 Placement-time pricing (operator's acceptance criterion)

> "אחרי שעבר זמן דיליי, הוא בודק מה המחיר כרגע ומניח את הפקודות."

The round-0 branch already re-runs on every tick while no order is live and no
leg is filled, so once it reads `mstate.mid` the price is by construction the
mid at the tick the order is actually submitted, not one carried over from
before `entry_delay_sec` expired. No new latching, no new state.

### 3.2 Unavailable mid

`mstate.mid` comes from `two_sided_mid_with_default`, which already substitutes
`0.50` for a leg it cannot price. Behaviour when the book prices nothing is
therefore **unchanged by this issue** and is owned by the companion issue #207.
Guard `mstate.mid is None` (the dataclass default, before the first snapshot)
by falling back to `0.50`, matching `:4438` exactly.

## 4. Acceptance criteria

1. With a two-sided book whose mid is `0.60` and `offset = 0.03`, the opening
   quotes are `0.57` / `0.37` — **not** `0.47` / `0.47`.
2. `resting_up + resting_down == round(1.0 - 2 * offset, 3)` for any mid in
   `[0.05, 0.95]`.
3. With `entry_delay_sec = 60`, the price used at placement reflects the mid on
   the placing tick, not the mid at window open.
4. A mid at 0.50 still produces the historical `0.47` / `0.47`, so every
   existing test that opens at 0.50 stays green.
5. Extreme mids clamp into `[0.01, 0.99]` and never emit a negative or >1 price.
6. The dead `up_mid` / `down_mid` / `locals()` code is gone.

## 5. Out of scope

- `two_sided_mid_with_default`'s 0.50 substitution (#207).
- The adverse-open gate's threshold source (#208).
- Stop-loss reference price (#209), leg chase trigger (#210),
  naked-leg timeout semantics (#211), re-entry coverage (#212),
  entry band anchoring (#213), live/backtest parity harness (#214).
- The advance pre-quote block at `:4230`. It prices a window whose book does not
  exist yet, and it is already suspended whenever entry controls are armed.
  Document why it stays at `0.50`; do not change it.
- Resets to `0.50 - offset` at `:1037`, `:2905`, `:3002` — these are
  initialisations before any book has arrived, not pricing decisions.
