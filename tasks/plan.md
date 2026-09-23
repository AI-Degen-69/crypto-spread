# Plan: Issue #311 — Jungle King Foundation: Golden-Dataset OFAT Parameter Range Manifest

Branch: `i311/jungle-king-foundation-ofat-manifest` | Issue: #311
Size tier: Small (2 new research files)
Task type: Research / Specs / Docs

## Summary & Goals
Establish `research/jungle-king/` as the dedicated space for evolutionary optimization on the certified golden dataset (`docs/golden-tick-dataset.md`).
Deliver:
1. `research/jungle-king/param_ranges.json`: Machine-readable OFAT parameter range manifest matching `BacktestParams` fields and `exit_thresh_by_slug.*`.
2. `research/jungle-king/README.md`: Human-readable checkbox list of all parameter ranges, OFAT methodology, and baseline definition.

## Dependencies & Risk
- Zero runtime risk: no changes to engine or CLI code.
- Strict schema validation: python one-liner assertion passes.

## Tasks

### Task 1: Initialize research directory & write `param_ranges.json` [Research/Specs] [x]
- **Files**: `research/jungle-king/param_ranges.json`
- **Depends on**: None
- **Size**: S
- **Verification**: Python JSON parsing & schema assertion command.
- **Details**:
  - Keys:
    - Tuning knobs: `offset`, `queue_gate`, `quote_shares`, `entry_delay_sec`, `entry_delay_pct`, `exit_reversal`, `enable_leg_chase`
    - Structural limits: `max_pair_cost`, `quote_range`, `dead_zone_val`, `dead_zone_unit`, `naked_leg_at_expiry`
    - Assumptions: `taker_fee_rate`, `merge_gas_usd`, `tick_size`, `min_quote_shares`
    - Exit thresholds: `exit_thresh_by_slug.default_5m`, `exit_thresh_by_slug.default_15m`, `exit_thresh_by_slug.btc-up-or-down-5m`, `exit_thresh_by_slug.sol-up-or-down-5m`, `exit_thresh_by_slug.btc-up-or-down-15m`, `exit_thresh_by_slug.sol-up-or-down-15m`
  - Values:
    - Lists of >= 3 items, sorted ascending, duplicate-free, containing the baseline defaults and spanning registry bounds.

### Task 2: Create human-readable `README.md` with checkbox layout [Docs] [x]
- **Files**: `research/jungle-king/README.md`
- **Depends on**: Task 1
- **Size**: S
- **Verification**: Markdown visual review & parameter parity check with `param_ranges.json`.
- **Details**:
  - Documents the OFAT rule ("vary one param, hold the rest at baseline").
  - Documents the exact baseline values (`scripts/backtest.py` + `BacktestParams`).
  - Formats parameter ranges as checkbox lists grouped by Tuning Knobs, Structural Limits, Execution Assumptions, and Exit Thresholds.

### Task 3: Automated verification & targeted test gate [Verification] [x]
- **Files**: None (CLI verification)
- **Depends on**: Task 1, Task 2
- **Size**: XS
- **Verification**:
  - Run the official issue #311 verification command:
    ```powershell
    python -c "import json; from dataclasses import asdict; from backtest.engine import BacktestParams; d=json.load(open('research/jungle-king/param_ranges.json')); fields=set(asdict(BacktestParams())); bad=[k for k in d if k not in fields and not k.startswith('exit_thresh_by_slug.')]; assert not bad, bad; assert all(isinstance(v, list) and len(v) >= 3 and len(set(map(str, v))) == len(v) for v in d.values()), 'empty or duplicate values'; print('OK:', len(d), 'parameters enumerated')"
    ```
  - Run targeted sweep tests:
    ```powershell
    python -m pytest tests/test_sweep_backtest.py -q
    ```

## Improvement Proposal (Adopted by default)
- **Proposal**: Group the parameters in `param_ranges.json` logically or include clear comments/annotations in `README.md` highlighting the baseline value for each parameter with `(baseline)` tag next to its checkbox, so operators can immediately see what "at baseline" means when inspecting any parameter.
