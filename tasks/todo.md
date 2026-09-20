# Issue #272 Planning Checklist

- [x] Confirm issue scope, capture-state vocabulary, and policy targets
- [x] Define one machine-readable target contract for verifier/API/UI
- [x] Add capture-state labels, reasons, and safe actions
- [x] Render two-target progress rows for every readiness metric
- [x] Add accessible floating tooltip for the readiness explanation
- [x] Verify tiny, exploratory, and near-research files in the real Tick Files tab
- [x] Run targeted tests and browser checks
- [x] Keep raw PASS/WARN/FAIL API compatibility and existing actions intact

Targeted verification command:
`python -m pytest tests/test_osc_dash_integration.py tests/test_verify_tick_data.py tests/test_theme_tokens.py -q`
