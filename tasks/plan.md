# Task Plan: Issue #83 — Merge Paired Timestamp Cells and Refine Column Separator in Cockpit Tables

## Overview
Merge duplicate timestamp cells across paired multi-leg rows in the Live Trading Cockpit's Open Orders (`#cockpitOrdersTable`) and Positions (`#cockpitPositionsTable`) tables into a single `rowspan` cell, relocate the pair status accent border to the leading outer edge of the `Time` cell, and remove the harsh vertical border divider currently placed between `Time` and `Market`.

---

### Task 1: Merge Timestamp Cells & Move Status Border in Open Orders Table (`#cockpitOrdersTable`)
- **Target File**: `server/osc_dash.py:3970-4025`
- **Details**:
  - In `renderCockpitUI`:
    - Compute `statusBorderColor` based on `grp.status`:
      - `Paired`: `var(--up)`
      - `Partial`: `var(--gold)`
      - `Cancelled`: `var(--dim)`
      - `Unpaired`: `var(--line)`
    - Construct `timeCell`:
      - `<td rowspan="${grp.rowspan}" class="mono ot-pair-lead" style="font-size:11px;color:var(--faint);vertical-align:top;border-left:2px solid ${statusBorderColor};padding-left:10px">${esc(timeStr)}</td>`
      - Where `timeStr` is `grp.legs[0]?.time && grp.legs[0].time !== '-' ? grp.legs[0].time : '-'`.
    - In `mktCell`:
      - Remove `border-left:2px solid ...` inline style.
      - Retain `rowspan="${grp.rowspan}"`, class `ot-pair-lead`, and padding `style="vertical-align:top;padding-left:10px"`.
    - In `grp.legs.forEach((leg, idx) => { ... })`:
      - For `idx === 0`: render `${timeCell}${mktCell}` followed by leg columns.
      - For `idx > 0`: omit both `timeCell` and `mktCell`, rendering only the leg-specific columns.
- **Verification**: `python -m pytest -q tests/test_orders_trades_table.py`

---

### Task 2: Merge Timestamp Cells & Move Status Border in Positions Table (`#cockpitPositionsTable`)
- **Target File**: `server/osc_dash.py:4040-4090`
- **Details**:
  - In `renderCockpitUI`:
    - Compute `statusBorderColor` based on `grp.status`:
      - `Paired`: `var(--up)`
      - `Partial`: `var(--gold)`
      - `Unpaired`: `var(--line)`
    - Construct `timeCell`:
      - `<td rowspan="${grp.rowspan}" class="mono ot-pair-lead" style="font-size:11px;color:var(--faint);vertical-align:top;border-left:2px solid ${statusBorderColor};padding-left:10px">${esc(timeStr)}</td>`
      - Where `timeStr` is `grp.legs[0]?.time && grp.legs[0].time !== '-' ? grp.legs[0].time : '-'`.
    - In `mktCell`:
      - Remove `border-left:2px solid ...` inline style.
      - Retain `rowspan="${grp.rowspan}"`, class `ot-pair-lead`, and padding `style="vertical-align:top;padding-left:10px"`.
    - In `grp.legs.forEach((leg, idx) => { ... })`:
      - For `idx === 0`: render `${timeCell}${mktCell}` followed by `Side`, `Size`, `Base Cost`, and `${pairSharedCells}` (`Market Value`, `Unrealized`, `Realized`).
      - For `idx > 0`: omit `timeCell`, `mktCell`, and `pairSharedCells`, rendering only leg-specific columns (`Side`, `Size`, `Base Cost`).
- **Verification**: `python -m pytest -q tests/test_orders_trades_table.py`

---

### Task 3: Update and Expand Unit & DOM Integration Tests
- **Target File**: `tests/test_orders_trades_table.py`
- **Details**:
  - In `test_cockpit_orders_trades_dom_rendering`:
    - Verify that `cockpitOrdersBody` has `rowspan="2"` on the `Time` cell.
    - Verify that `cockpitOrdersBody` does not render a second `Time` cell for the secondary leg (`14:05:01`).
    - Verify that `Time` cell contains `border-left:2px solid` with pair status color (`var(--up)`).
    - Verify that `mktCell` does not contain `border-left:2px solid`.
  - In `test_cockpit_orders_table_cancelled_orders_dom`:
    - Verify `Partial` and `Cancelled` groups apply gold (`var(--gold)`) and dim (`var(--dim)`) borders respectively to the merged `Time` cell.
  - Add a dedicated multi-leg positions DOM rendering test:
    - Verify paired positions have a single merged `Time` cell with `rowspan="2"` and `border-left:2px solid var(--up)`.
    - Verify secondary position leg does not render duplicate timestamp or market cells.
- **Verification**: `python -m pytest -q tests/test_orders_trades_table.py` and `python -m pytest -q`
