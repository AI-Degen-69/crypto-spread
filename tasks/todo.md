# Tasks: Issue #61 LTR Layout & English Localization

- [ ] Task 1: Add automated integration tests in `tests/test_osc_dash_integration.py` (RED)
  - Acceptance: Tests assert `dir="ltr"`, `lang="en"`, absence of `dir="rtl"`, `.tbl th` left-aligned, and 0 Hebrew characters in `server/osc_dash.py`.
  - Verify: `python -m pytest tests/test_osc_dash_integration.py -k "ltr or hebrew"` fails on existing codebase.
  - Files: `tests/test_osc_dash_integration.py`

- [ ] Task 2: Refactor HTML root attributes, CSS alignments, Cockpit control bar, and parameter form grid in `server/osc_dash.py`
  - Acceptance: Document root is `<html lang="en" dir="ltr">`, `.tbl th` and `.form-group label` are left-aligned, Cockpit header is left-anchored, action buttons right-anchored, and form grid reads LTR.
  - Verify: Integration tests for LTR and CSS pass.
  - Files: `server/osc_dash.py`

- [ ] Task 3: Translate all remaining Hebrew strings across all tabs, modals, and dynamic JS in `server/osc_dash.py`
  - Acceptance: All 138 lines containing Hebrew characters are replaced with clean English strings.
  - Verify: Regex scan for `[\u0590-\u05ff]` finds 0 lines in `server/osc_dash.py`.
  - Files: `server/osc_dash.py`

- [ ] Task 4: Full verification and regression suite run
  - Acceptance: Full test suite passes (`python -m pytest -q`).
  - Verify: `python -m pytest -q` reports 100% pass with 0 errors.
  - Files: none (verification)
