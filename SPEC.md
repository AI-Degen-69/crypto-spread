# SPEC.md — Issue #214: the engine parity harness

## 1. Problem Statement

`strategy/live_trader.py` and `backtest/engine.py` implement the same strategy twice. They
share knob *names* through `BacktestParams.param_spec()` (issue #164), which is what makes
divergence invisible: the dashboard shows one name and one number for two different meanings.

Three divergences were found by hand in a single afternoon and have since been fixed
(#204/#217, #206/#218, #207/#219). Nothing in the repo would have caught them, and nothing
would catch the next one. The rules session of 2026-09-16 then found three more in a few
hours, the largest being that **the backtest ends a window at the first merge while the live
engine opens a fresh round**, so the backtest has been under-reporting profit per window.

## 2. What this issue delivers

**The harness, and only the harness.** The strategy rules themselves were redefined in the
same session and are tracked separately (#224-#233); `docs/engine-decision-rules.md` is their
definition. This issue builds the machinery that holds both engines to whatever those rules
say, and seeds it with the cases that are already settled.

- **G1 — A harness.** Given one synthetic book sequence and one parameter set, drive the live
  decision path and `_simulate_window`, and compare their outcomes on a declared surface.
- **G2 — Seed scenarios.** The three fixed divergences (#204, #206, #207) and the
  entry-anchored stop (#209) are pinned so the fixes cannot silently unwind.
- **G3 — An extension point.** Each rule issue adds its own scenarios to this harness rather
  than inventing a second way to compare engines.

## 3. The parity contract

**Live `mode="paper"` versus `_simulate_window`.** Live paper mode simulates fills from the
book and the WebSocket tape; the backtest simulates them from the snapshot and its tape delta.
Under the single fill rule agreed in #226 these are the same rule, which is what makes the
comparison meaningful.

Until #226 lands, the harness runs against the existing model that matches live paper mode.
The harness must not hard-code a model name — it reads whatever the fill rule currently is, so
that #226 changes the engines and not the harness.

## 4. Comparable surface

Parity is asserted on the decision-visible subset of `WindowResult`:

`entered`, `filled_up`, `filled_down`, `entry_price_up`, `entry_price_down`, `pair_captured`,
`exit_taken`, `exit_side`, `chased_leg`, and the number of completed rounds.

Explicitly **not** compared: `pnl_cents`, `fees_cents`, `settlement_mid`, `settle_source`,
`class_label`, `max_up`, `max_down`, `n_snaps` — accounting and classification the live engine
does not compute per window. Forcing them in would mean building a second P&L model to prove
the first one.

## 5. Acceptance Criteria

- [ ] `snaps_to_polls` converts a backtest snap sequence into live `poll_data`, tick by tick.
- [ ] `live_outcome` runs a `LiveTraderEngine(load_persisted=False)` in `mode="paper"` over
      that sequence and returns the §4 surface.
- [ ] `assert_parity` runs both engines on the same snaps and the same parameters and fails
      with a readable diff naming the first field that disagrees and the tick it disagreed on.
- [ ] Seed scenarios: balanced open through to a merged pair; opening quote anchored to the
      real mid (#206); pair-cost cap not blocking quoting (#204); unpriceable leg skipping the
      window (#207); stop anchored to the entry price (#209).
- [ ] A divergence the harness finds is recorded as `xfail(strict=True)` with an issue number,
      never fixed here — so the xfail goes stale loudly the day it is fixed.
- [ ] `tests/test_engine_parity.py` runs in under 5 seconds on synthetic snaps.

## 6. Out of Scope

- **Every strategy rule change.** Tracked in #224-#233 against
  `docs/engine-decision-rules.md`. This issue changes no behaviour in either engine.
- **Extracting the shared decision logic into one module.** `_update_market_strategy` is
  ~1,000 lines entangled with CLOB calls, WebSocket state, engine locks, telemetry and order
  placement; `_simulate_window` is ~560 pure lines. Deferred by operator decision, with the
  rules document as the map any future extraction starts from.
- P&L, fee and settlement parity (see §4).
