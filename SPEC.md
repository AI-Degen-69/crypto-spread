# SPEC — Issue #224: invariants (no invented numbers, one window clock)

Binding while `fix/window-clock-and-no-invented-numbers-224` is live. Supersedes the #214
harness spec, which is preserved at commit `5f1f8b8` and in issue #214.

The agreed rule text is `docs/engine-decision-rules.md` (Invariant 0 and Invariant 1). This
file is the executable scope: what the code must do to match that text.

## Goals

**G1 — No decision path substitutes a constant for a missing book value.**
Two fabrications remain. The stop exit price falls back to `0.40`
(`strategy/live_trader.py:1735`), and the re-entry mid falls back to `0.50`
(`strategy/live_trader.py:4744`). Both are removed. When the value cannot be resolved from
the book, the engine does not act on that tick and re-evaluates on the next one.

**G2 — One definition of the window clock, read from market metadata only.**

```
window_length = end_ts - start_ts
elapsed       = now - start_ts           # live
elapsed       = snapshot_ts - start_ts   # backtest
```

No other source. The slug is not parsed for a duration, and the snapshot index is not counted
as seconds.

**G3 — A window with no usable clock is not traded.**
If `start_ts` and `end_ts` are not a usable pair, there is no clock, no time gate may be
evaluated, and the window is skipped in both engines. This is a visible skip, not a silent
default.

## What "usable pair" means

Both values are present and finite, and `end_ts > start_ts`. Nothing else is asserted: absolute
epoch position is not checked, because tests and replays legitimately use small or synthetic
timebases, and an absolute-value check would reject them while catching no real fault that
`end_ts > start_ts` misses.

For `elapsed`, the tick's own timestamp must also be present and finite. A snapshot without one
carries no clock reading and is skipped.

## Acceptance criteria

| # | Criterion | Where |
|---|---|---|
| A1 | The literal `0.40` stop-exit fallback is gone; the price resolves through `_resolve_exit_bid` | `strategy/live_trader.py` |
| A2 | When every resolution stage fails, the position is held, no trade is recorded, and the exit re-evaluates next tick | `strategy/live_trader.py` |
| A3 | The literal `0.50` re-entry mid is gone; no mid means no re-entry this tick | `strategy/live_trader.py` |
| A4 | `(900.0 if "15m" in slug else 300.0)` appears nowhere | `strategy/live_trader.py` |
| A5 | `elapsed = float(s_idx)` appears nowhere | `backtest/engine.py` |
| A6 | A live window whose metadata gives no usable pair is not traded | `strategy/live_trader.py` |
| A7 | A backtest window whose first snapshot gives no usable pair is not traded, and reports why | `backtest/engine.py` |
| A8 | Tests cover each of: missing exit bid, missing mid at re-entry, missing timestamps, a slug with no duration substring | `tests/` |

## Out of scope

- The parity harness for #214. It is the next piece of work, not this one.
- Every rule issue #225-#233. This issue changes only the two invariants above.
- `taker_fee_rate` being dead in the live engine. Real, separate, unfiled.
- The `duration` field the collector writes. It is a series label used to key the per-duration
  stop threshold, not a clock, and no time gate may read it after this change.
