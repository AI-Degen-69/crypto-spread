# SPEC — Issue #225: the entry anchor

Binding while `fix/entry-anchor-repriced-two-sided-225` is live. Supersedes the #224 spec,
preserved at commit `a6c88d2`.

Rule text: `docs/engine-decision-rules.md`, rule 1 `entry_anchor`. This file is the executable
scope.

## The rule

```
resting_up   = clamp(round(mid - offset, 3), 0.01, 0.99)
resting_down = clamp(round((1 - mid) - offset, 3), 0.01, 0.99)
```

Two settled points, both of which the backtest gets wrong.

**G1 — repriced every tick until an order actually exists.** The submitted price is the mid at
placement time. It latches only once the quote is live. Live already does this; the backtest
anchors once at delay expiry and never re-anchors, so whenever anything holds placement the two
engines drift apart.

**G2 — `mid` means the two-sided mid, and nothing else.** If either leg cannot be priced there
is no anchor and no quote is placed. Live already does this (#207); the backtest prefers the
recorded one-sided `s["mid"]` — the collector's up-leg reading — and quotes anyway.

## What "the quote is live" means in the backtest

There is no order object. A quote is live once it has reached fill detection on some earlier
tick and has not been cancelled since — the condition live spells as
`order_id_up or order_id_down`. `entry_cancelled` is live's cancelled-orders state and re-opens
repricing, which is what lets a re-entry quote at the price of its own tick.

## Acceptance criteria

| # | Criterion |
|---|---|
| A1 | The backtest reprices the anchor on every tick until the quote is placed |
| A2 | Both engines anchor only on a two-sided mid |
| A3 | An unpriceable book produces no quote on **either** leg and latches nothing |
| A4 | `s["mid"]` is not read as an anchor anywhere, including the re-entry re-quote |
| A5 | A parity test drives both engines over a book that goes one-sided mid-window and asserts the same quote and the same abstention |

## Expected consequences

- **Historical sweep numbers change.** The point of the issue: the old numbers described entries
  the live engine would not have placed.
- **Three settlement stages stop being reachable end to end.** `latched_complement_ask`,
  `redeemed` and `unresolved` all need a leg the ladder has nothing latched for — which, once
  entry requires a two-sided mid on both legs, is a leg that was never quoted. The stages stay
  in the resolver and keep their tests; those tests now call it directly, like the empty-window
  case already did.

## Out of scope

- The full parity harness (#214). The test added here is a scenario, not the harness.
- Every other rule issue, #226-#233.
- The `entry_band` / `adverse_open` gates (#228) and the pair-cost gate (#227), even where the
  fixtures touched here also exercise them.
