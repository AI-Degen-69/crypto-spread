# Plan — Issue #394: Keep focus in the Sweep Visual anchor field across a progress re-render

Branch: `i394/keep-focus-in-the-sweep-visual-anchor-field-ac` | Issue: #394

## Overview
While a parameter sweep is streaming progress updates, re-rendering `renderSweepVisual` detaches and rebuilds `btSweepMeta`. The anchor wrapper node (`btSweepAnchorWrap`) is preserved and re-appended (keeping the typed value), but DOM detachment causes `document.activeElement` to blur to `document.body`. This interrupts user typing on every progress tick.

We will record whether `btSweepCenter` was focused immediately prior to detaching/clearing `meta.innerHTML`, and restore focus to `btSweepCenter` via `.focus({ preventScroll: true })` after the title row and tail elements are re-attached to the live DOM.

## CodeRabbit Intake Summary
- **Adopted:** Focus check `anchorWasFocused = (keepAnchorWrap && anchorInput && document.activeElement === anchorInput)` before clearing `meta.innerHTML = ''`, and restoring focus once reattached. Added dedicated Node regression test.
- **Rejected:** Caret/selection restoration (HTML number inputs do not support text selection APIs and throw in standard browsers).
- **Status:** Verified and ready.

## Tasks

- [x] **Task 1: Add regression test in `tests/test_osc_dash_integration.py`**
  - **Size:** S
  - **Domain:** `[Test/Integration]`
  - **Files:** `tests/test_osc_dash_integration.py`
  - **Depends on:** None
  - **Details:** Add `test_sweep_progress_rerender_keeps_anchor_focus` using a Node test harness that models parent-child connection, `innerHTML` reset, and focus tracking. Confirm that the test fails against the current unpatched code.
  - **Verification:** `python -m pytest tests/test_osc_dash_integration.py -k test_sweep_progress_rerender_keeps_anchor_focus -q` (expected red before fix).

- [x] **Task 2: Fix focus preservation in `renderSweepVisual` progress branch**
  - **Size:** XS
  - **Domain:** `[UI/Frontend]`
  - **Files:** `server/osc_dash.py`
  - **Depends on:** Task 1
  - **Details:** In `renderSweepVisual` progress branch (`server/osc_dash.py`), look up `btSweepCenter`, record `anchorWasFocused`, and call `anchorInput.focus({ preventScroll: true })` after `meta.appendChild(titleWrap)` and the tail elements are attached. Update inline comments explaining focus restoration.
  - **Verification:** `python -m pytest tests/test_osc_dash_integration.py -k test_sweep_progress_rerender_keeps_anchor_focus -q` (passes green).

- [x] **Task 3: Run targeted integration suite and verify regression safety**
  - **Size:** XS
  - **Domain:** `[Verification]`
  - **Files:** `server/osc_dash.py`, `tests/test_osc_dash_integration.py`
  - **Depends on:** Task 2
  - **Details:** Run targeted dashboard integration suite to verify no regressions in sweep visual, DOM helpers, or anchor plumbing.
  - **Verification:** `python -m pytest tests/test_osc_dash_integration.py -q`.
