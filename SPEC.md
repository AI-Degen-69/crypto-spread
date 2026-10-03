# SPEC.md — Issue #419: Unify sub-dollar price params to whole-number cents inputs and displays

Locked by Station II (`ii-plan-issue #419`). Branch: `i419/unify-sub-dollar-price-params-to-whole-number-cent`.

## Goal
Every sub-dollar price parameter on every dashboard surface accepts and displays
whole-number cents (`2`, `5`, `10.5` for 0.1c resolution) with one `c` format —
no `0.105` vs `0.05` vs `0.020` mixing remains.

## Conversion boundary (locked)
Cents exist only at the dashboard edge. The trading engine, `/api/backtest`,
`/api/live/*`, CLI flags (`scripts/backtest.py`, `scripts/sweep_backtest.py`),
sweep drivers, saved templates, and `params_hash()` stay in dollar floats.
Entering `5` in a converted field runs exactly the strategy `0.05` runs today.

## Scope: converted knobs (5 registry fields)
`offset`, `max_pair_cost`, `quote_range` (lo/hi), `exit_thresh_by_slug`
(4 backtest inputs + cockpit input), `exit_reversal` — on the backtest tab,
the trading platform (cockpit), the sweep held-parameters card, price-axis
tick labels, the quote-range display, the registry, and `/api/params/spec`.

## Explicitly out of scope
Engine internals (dollar-float fields, fill rule, CLI flags), venue constants
(`tick_size`, `taker_fee_rate`, `merge_gas_usd`, `min_quote_shares`), non-price
inputs (shares, pct of window, seconds, balance, P&L, dead zone), Jungle King
manifest values (read-only mirror of `research/jungle-king/` — labels only),
market-result displays (fills, mean pair cost, trade prices, positions).

## Acceptance criteria (from the issue, verbatim intent)
- [ ] Every sub-dollar price input on the backtest tab and the trading platform
  accepts cents (whole numbers, at most one decimal for 0.1c values like 10.5)
  with existing invalid-hint styling on bad input.
- [ ] Every rendering of those values (input values, sweep held rows, sweep
  price-axis tick labels, quote-range display) uses the same cents format.
- [ ] Entering `5` in a converted field runs exactly the strategy `0.05` runs
  today (UI-edge conversion; engine/API/CLI dollar contract unchanged).
- [ ] Registry labels/units plus `/api/params/spec` are the single source: no
  hard-coded `$` price label remains for a converted knob.
- [ ] Targeted tests pass:
  `python -m pytest tests/test_osc_dash_integration.py tests/test_param_registry.py -q`

## Edge cases
- `10.5c` round-trips exactly (`0.105`); validator rejects `10.55`, empty,
  non-finite, and out-of-range input; `quoteLo < quoteHi` enforced (both marked).
- Converted dollar values (e.g. `0.05`) are never integers in the API
  `normalize_*` ranges, so the server heuristics do not double-fire.
- Blank sweep-center input stays `null`; explicit zero stays zero.

## Open questions — all resolved from code + defaults, nothing to ask
1. Boundary → UI edge only (sweep drivers, templates, `/api/backtest` speak
   dollars today; CodeRabbit Design Choice 1, adopted).
2. Sub-cent → allow one decimal of a cent (`step="0.1"`, venue tick `0.001`).
3. Range limits (`quote_range`, `max_pair_cost`) → same cents convention.
4. Labels → ASCII `c`; registry keeps canonical `$` unit plus a `display`
   block (see improvement proposal in `tasks/plan.md`).
