# Tasks: Issue #413 — Add Save-as-Template to the Backtest tab

- [ ] **Task 1: Store Module & Documentation** (`backtest/templates.py`, `docs/run-conventions.md`, `docs/glossary.md`)
  - Implement `normalize_name`, `template_path`, `validate_record`, `save_template`, `read_template`, `list_templates`, `delete_template`.
  - Update `docs/run-conventions.md` and `docs/glossary.md`.
  - Unit tests in `tests/test_backtest_templates.py`.

- [x] **Task 2: Server Endpoints & Run Registry** (`server/osc_dash.py`, `tests/test_backtest_templates.py`)
  - Add `BACKTEST_TEMPLATES_DIR` and `_COMPLETED_RUNS` registry (bounded to 32 items).
  - Include `run_id` in `/api/backtest` and `/api/backtest/stream` responses.
  - Implement POST/GET/DELETE `/api/backtest/templates` endpoints with origin checks and stale hash validation (409).
  - Add endpoint test suite in `tests/test_backtest_templates.py`.

- [x] **Task 3: UI Controls & Tab Integration** (`server/osc_dash.py`, `tests/test_osc_dash_integration.py`)
  - Add `btnSaveTemplate` button and `#btSecTemplates` list card in Backtest tab.
  - Wire `_btSaveableRunId`, `saveBacktestTemplate()`, `loadBacktestTemplateList()`, `loadBacktestTemplate()`, `applyBacktestTemplate()`, `deleteBacktestTemplate()`.
  - Add JS/HTML integration tests in `tests/test_osc_dash_integration.py`.

- [x] **Final Verification Gate**
  - `tests/test_backtest_templates.py`: 18 passed.
  - `tests/test_osc_dash_integration.py` + `tests/test_theme_tokens.py`: 348 passed, 1 pre-existing env failure (`test_sweep_categorical_axis_rendering_node` — Windows `node -e` length limit, fails identically on base `7b914a9`).
  - Run `python -m pytest tests/test_backtest_templates.py -q`.
  - Run `python -m pytest tests/test_osc_dash_integration.py tests/test_theme_tokens.py -q`.
