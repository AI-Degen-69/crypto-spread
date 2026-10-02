# Implementation Plan — Issue #401

Branch: `i401/show-current-total-windows-progress-sweep-visual` | Issue: #401

## Classification & Context
- **Stack:** Python 3.12 (FastAPI), vanilla JavaScript & CSS in `server/osc_dash.py`.
- **Size tier:** Small (1 dashboard file, 1 test file).
- **Task type:** Design/UI, Code (`frontend-ui-engineering`, `test-driven-development`).
- **CodeRabbit Plan Intake:**
  - Adopted: Card-local timer with `sweepElapsedTime` node; pure `sweepWindowsCounter(done, total)` helper; unified progress stats row replacing redundant rows counter; updated `sweepProgressText` to "X/Y windows"; static button text `⏳ Sweeping…`; test coverage for all new UI states.
  - Rejected: Over-engineered token classes; unnecessary new files.

---

## Tasks

### [x] Task 1: [Design/UI] [Code] Pure helper `sweepWindowsCounter` and updated `sweepProgressText`
- **Size:** S
- **Target files:** `server/osc_dash.py`
- **Depends on:** None
- **Description:**
  - Implement pure JS helper `sweepWindowsCounter(done, total)`:
    - If `total === null || total === undefined`: returns `${done || 0}/?`
    - Else: returns `${done || 0}/${total}`
  - Update `sweepProgressText(data)`:
    - Numeric state returns `${data.rows_done || 0}/${data.rows_total} windows` (was `row ${data.rows_done || 0}/${data.rows_total}`).
- **Verification:** Node-harness extraction & pytest unit tests in `tests/test_osc_dash_integration.py`.

### [x] Task 2: [Design/UI] [Code] Card-local Elapsed timer and unified stats row in `renderSweepVisual`
- **Size:** S
- **Target files:** `server/osc_dash.py`
- **Depends on:** Task 1
- **Description:**
  - In `renderSweepVisual` progress branch:
    - Remove redundant "Sweeping ... rows · pct%" span.
    - Render `Windows` stat using `sweepWindowsCounter(data.rows_done, data.rows_total)`.
    - Render `Elapsed` stat with ID `sweepElapsedTime`, formatted via `fmtElapsed(performance.now() - window._btSweepStartTime)` (fallback `0s`).
  - In `renderSweepVisual` final branch:
    - Update `Windows` stat to display `sweepWindowsCounter(data.n_windows, data.n_windows)` (e.g. `4210/4210`).
  - In `runSweepVisual`:
    - Set Run button once to static `⏳ Sweeping…` (disabled).
    - In the 500ms `setInterval`, update `sweepElapsedTime` element's `textContent` instead of changing Run button text.
    - In `finally` block and `stopBacktestRun`: reset timer interval, restore Run button label to `▶ Run Sweep Visual`, clear `window._btSweepStartTime`.
  - Add CSS for tabular numbers in `.sweep-stat-v` or `#sweepElapsedTime`.
- **Verification:** Static string checks and targeted integration tests.

### [x] Task 3: [Tests] Comprehensive integration tests for Sweep Visual progress and timer
- **Size:** S
- **Target files:** `tests/test_osc_dash_integration.py`
- **Depends on:** Task 1, Task 2
- **Description:**
  - Update `test_sweep_progress_text_three_states` for the updated windows wording.
  - Add test for `sweepWindowsCounter` behavior (`7/null` -> `7/?`, `0/0` -> `0/0`, `4210/4210` -> `4210/4210`).
  - Add assertions verifying `sweepElapsedTime` exists in progress markup, `rows ·` is removed, Run button uses static `⏳ Sweeping…`, and timer resets cleanly.
- **Verification:** `python -m pytest tests/test_osc_dash_integration.py -k sweep -q`.
