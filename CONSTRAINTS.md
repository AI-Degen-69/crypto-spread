# Constraints — Issue #311: Jungle King Foundation (param_ranges manifest)

## Scope & Zero Regressions
- **Scope**: Create `research/jungle-king/param_ranges.json` and `research/jungle-king/README.md`.
- **Out of scope**: No changes to `backtest/engine.py`, `scripts/sweep_backtest.py`, `scripts/backtest.py`, or any trading/dashboard modules.
- **Zero test regressions**: Existing test suite must remain untouched and passing.

## Manifest Schema Contract
1. Valid JSON in `research/jungle-king/param_ranges.json`.
2. Every top-level key must be either a field in `BacktestParams` or start with `exit_thresh_by_slug.`.
3. Every value must be a non-empty list of length >= 3.
4. No duplicate values within any list (`len(set(map(str, v))) == len(v)`).
5. Values must be sorted ascending (where ordering applies; tuples/strings excepted).
6. The baseline default value of each parameter must be included in its candidate list.
7. Verification command must pass cleanly:
```powershell
python -c "import json; from dataclasses import asdict; from backtest.engine import BacktestParams; d=json.load(open('research/jungle-king/param_ranges.json')); fields=set(asdict(BacktestParams())); bad=[k for k in d if k not in fields and not k.startswith('exit_thresh_by_slug.')]; assert not bad, bad; assert all(isinstance(v, list) and len(v) >= 3 and len(set(map(str, v))) == len(v) for v in d.values()), 'empty or duplicate values'; print('OK:', len(d), 'parameters enumerated')"
```

## Documentation Contract
- `research/jungle-king/README.md` must mirror every parameter in checkbox format (`[ ] value`).
- Must clearly define the OFAT rule ("vary one param, hold the rest at baseline").
- Must explicitly state the baseline parameter configuration (combining `BacktestParams` defaults and `scripts/backtest.py` CLI defaults).
- Must categorize knobs into Tuning Knobs, Structural Limits, and Execution Assumptions per `_PARAM_GROUPS` and ADR-0003.
