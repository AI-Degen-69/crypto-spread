# SPEC — Issue #227: `max_pair_cost` caps the chase only

Binding while `fix/max-pair-cost-chase-only-227` is live. Supersedes the #226 spec,
which is merged and closed. Per-issue working file (`docs/git-workflow.md` §5) —
not an architecture document.

The rule itself is already written and agreed: `docs/engine-decision-rules.md` §4
(`max_pair_cost`) and `docs/adr/0003-structural-limits-separate-from-tuning-knobs.md`.
Those two files are the definition. This spec is the work that makes the code match
them.

## Goal

One ceiling, in one place, meaning one thing: the most the **leg chase** may pay to
complete a pair. A binary pair settles at exactly 1.00, so a cap above 1.00
authorises a guaranteed loss, and both engines refuse it.

Everything else that currently wears the name goes.

## Acceptance criteria

1. **One name.** `BacktestParams.pair_cost_gate` is renamed `max_pair_cost`,
   matching `LiveTraderEngine.max_pair_cost`. No alias, no back-compat shim.
2. **One default: 0.99** in both engines.
3. **One range: `[0.50, 1.00]`** in both engines, on every surface. The per-surface
   override that gave the Backtest tab `(0.0, 2.0)` and the Cockpit `(0.50, 1.00)`
   is deleted, and `BacktestParams.__post_init__` raises `ValueError` outside the
   range — so a research driver constructing the dataclass directly cannot hold a
   value the API would have refused.
4. **The backtest's entry-side pair-cost block is gone**, together with the
   fill-detection skip it fed. The surviving gate on that branch is `queue_gate`
   alone.
5. **The two-ask entry test in the research simulators is gone.** `ev_lab.py` and
   `sim2.py` test `(up_ask + dn_ask) <= cap` at entry; the two asks of a binary pair
   always sum to ~1.00–1.01, so the test carries no information (issue text,
   operator decision 2026-09-16).
6. **The chase ceiling is unchanged in behaviour and identical in both engines:**

   ```
   max_bid = floor((max_pair_cost - entry_price_of_filled_leg) * 100) / 100
   ```

   floored to whole cents, quote raised only, never lowered.
7. **A parity test asserts 6** by driving both engines over one shared snapshot
   sequence, as `tests/test_fill_rule_parity.py` (#226) and
   `tests/test_entry_anchor_parity.py` (#225) do.
8. **The knob is recorded as a structural limit**, not a tuning knob.
9. **No off switch.** `max_pair_cost = 0` no longer disables anything: the value is
   out of range and the dataclass refuses it. The dashboard's ON/OFF toggle for the
   Backtest pair-cost field is deleted with the gate it switched.

## Edge cases

- **`entry_price` above the cap.** `floor((0.99 - 1.00) * 100) / 100 = -0.01`;
  `min(ask, -0.01)` is below any resting quote, so the chase does not raise and no
  quote is placed at a negative price. Already true in both engines today; the
  parity test pins it.
- **No ask on the leg being chased.** Both engines already require an ask before
  chasing. Unchanged.
- **A window whose resting pair costs more than the cap** — e.g. `offset = 0.005`,
  pair cost `0.99`. Today the backtest refuses to enter it and skips fill detection.
  After this change it enters normally, and the offset is the only thing that
  decides entry cost. **This is a deliberate behaviour change and the point of the
  issue.**
- **`params_hash()` changes for every config** — the field name is part of the hash
  input. Cached sweep artifacts keyed by hash are invalidated, exactly as in #226.
  Accepted.

## Out of scope

- **`strategy/config.py:649` `MakerConfig.max_pair_cost = 0.995`.** A different bot
  (the powerwinner-derived SPREAD-1 maker), a different rule — it stops *quoting* a
  side, it does not cap a chase. Same words, different engine. Not touched.
- **The parameter-class mechanism** (a declared `class` per registry field, sweep
  drivers refusing structural limits without an opt-in). ADR-0003 assigns that to
  issue #233. Here the classification is recorded in the registry entry's text and
  in the rule document; #233 builds the machinery.
- **The duplicated `elif target == max_bid and target >= resting` branches** in the
  live chase (`live_trader.py:4504`, `:4521`, `:5096`, `:5137`). They are unreachable
  duplicates of the `if` above them. Noted, not removed — deleting them is a
  behaviour-neutral cleanup that belongs with #214's parity harness, not here.
