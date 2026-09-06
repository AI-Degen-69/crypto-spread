# SPEC: Merge Paired Timestamp Cells and Refine Column Separator in Cockpit Tables (Issue #83)

## 1. Objective
Refactor the table rendering for Open Orders (`#cockpitOrdersTable`) and Positions (`#cockpitPositionsTable`) in the Live Trading Cockpit dashboard (`server/osc_dash.py`) so that multi-leg paired markets render a single, merged `Time` cell spanning across the entire pair (`rowspan="${grp.rowspan}"`), move the pair status accent border to the leading left edge of the row / Time cell, and remove the harsh vertical border divider between Time and Market.

## 2. Background & Problem Statement
In the Cockpit's Open Orders and Positions tables:
1. Binary market legs (UP and DOWN) are grouped by market.
2. While the `Market` cell spans both legs using `rowspan`, the `Time` cell currently generates an independent `<td>` for every individual leg. This results in duplicate timestamps stacked vertically for the same pair.
3. The `Market` cell currently carries `border-left: 2px solid ...`, creating a stark vertical divider directly between `Time` and `Market`. This disrupts column flow, breaks visual alignment, and disconnects the timestamp from its market card.

## 3. Scope

### In Scope
1. **`server/osc_dash.py` — Open Orders Table (`#cockpitOrdersTable`)**:
   - Merge `Time` cell across grouped legs using `rowspan="${grp.rowspan}"` (rendered once on `idx === 0`).
   - Relocate the status accent border (`border-left: 2px solid <color>`) to the leading `Time` cell:
     - `Paired` -> `var(--up)` (green)
     - `Partial` -> `var(--gold)` (gold)
     - `Cancelled` -> `var(--dim)` (gray/dim)
     - `Unpaired` -> `var(--line)`
   - Remove the harsh `border-left: 2px solid ...` from the `Market` `<td>`.
   - On secondary legs (`idx > 0`), omit both `Time` and `Market` cells.

2. **`server/osc_dash.py` — Positions Table (`#cockpitPositionsTable`)**:
   - Merge `Time` cell across grouped legs using `rowspan="${grp.rowspan}"` (rendered once on `idx === 0`).
   - Apply status accent border (`border-left: 2px solid <color>`) to the leading `Time` cell:
     - `Paired` -> `var(--up)` (green)
     - `Partial` -> `var(--gold)` (gold)
     - `Unpaired` -> `var(--line)`
   - Remove `border-left: 2px solid ...` from the `Market` `<td>`.
   - On secondary legs (`idx > 0`), omit `Time`, `Market`, and pair-level shared cells (`Market Value`, `Unrealized`, `Realized`).

3. **`tests/test_orders_trades_table.py`**:
   - Update existing Node.js DOM tests and add new tests verifying:
     - `Time` cell rendered once per group with `rowspan` attribute matching group size.
     - `border-left: 2px solid` applied to the `Time` cell according to group status.
     - `Market` cell does not contain `border-left: 2px solid`.
     - Non-leading rows (`idx > 0`) do not render redundant `Time` or `Market` cells.
     - Both Orders and Positions tables pass all assertions.

### Out of Scope
- Modifying backend order generation, state management, or execution logic in `strategy/live_trader.py`.
- Modifying Tab 3 (Closed Trades), which does not use multi-leg paired row grouping.
- Altering column order, column widths, or table data schemas.

## 4. UI & DOM Architecture

### Orders Row HTML Structure
```html
<!-- Leading Row (idx === 0) -->
<tr class="ot-pair-lead">
  <td rowspan="2" class="mono ot-pair-lead" style="font-size:11px;color:var(--faint);vertical-align:top;border-left:2px solid var(--up);padding-left:10px">
    14:05:00
  </td>
  <td rowspan="2" class="ot-pair-lead" style="vertical-align:top;padding-left:10px">
    <div style="font-weight:700;font-size:12.5px;color:var(--tx)">...</div>
    <div style="display:flex;align-items:center;gap:6px;margin-top:4px">...</div>
  </td>
  <td><span class="ot-tag ot-tag-up">Up</span></td>
  <td class="mono">$0.48</td>
  <td class="mono">5</td>
  <td class="mono">0</td>
  <td class="mono">$2.40</td>
  <td><span class="pill pill-mono">OPEN</span></td>
  <td><button class="btn btn-danger cancel-order-btn">✖ Cancel</button></td>
</tr>
<!-- Follow-up Row (idx === 1) -->
<tr>
  <td><span class="ot-tag ot-tag-down">Down</span></td>
  <td class="mono">$0.48</td>
  <td class="mono">5</td>
  <td class="mono">0</td>
  <td class="mono">$2.40</td>
  <td><span class="pill pill-mono">OPEN</span></td>
  <td><button class="btn btn-danger cancel-order-btn">✖ Cancel</button></td>
</tr>
```

### Positions Row HTML Structure
```html
<!-- Leading Row (idx === 0) -->
<tr class="ot-pair-lead">
  <td rowspan="2" class="mono ot-pair-lead" style="font-size:11px;color:var(--faint);vertical-align:top;border-left:2px solid var(--up);padding-left:10px">
    14:01:00
  </td>
  <td rowspan="2" class="ot-pair-lead" style="vertical-align:top;padding-left:10px">
    <div style="font-weight:700;font-size:12.5px;color:var(--tx)">...</div>
    <div style="display:flex;align-items:center;gap:6px;margin-top:4px">...</div>
  </td>
  <td><span class="ot-tag ot-tag-up">Up</span></td>
  <td class="mono">5.00</td>
  <td class="mono">$0.480</td>
  <td rowspan="2" class="mono ot-pair-lead" style="vertical-align:middle;font-weight:600">$5.00</td>
  <td rowspan="2" class="mono ot-pair-lead" style="vertical-align:middle;font-weight:700;color:var(--up)">+$0.10 (+2.1%)</td>
  <td rowspan="2" class="mono ot-pair-lead" style="vertical-align:middle;font-weight:700;color:var(--tx)">--</td>
</tr>
<!-- Follow-up Row (idx === 1) -->
<tr>
  <td><span class="ot-tag ot-tag-down">Down</span></td>
  <td class="mono">5.00</td>
  <td class="mono">$0.480</td>
</tr>
```

## 5. Acceptance Criteria
- [ ] For paired orders with multiple legs, the `Time` cell in `#cockpitOrdersBody` spans the entire group using `rowspan` instead of repeating separate timestamp cells.
- [ ] For paired positions with multiple legs, the `Time` cell in `#cockpitPositionsBody` spans the entire group using `rowspan`.
- [ ] The heavy 2px vertical border between the Time and Market columns is removed from `mktCell`.
- [ ] A refined status accent border is positioned at the leading outer edge of the pair row/time cell (`border-left: 2px solid ...`) without an abrupt divider between Time and Market.
- [ ] Unit and DOM tests verify the presence of merged timestamp cells (`rowspan`) and proper rendering under mixed, cancelled, and paired states.
- [ ] All test suites pass: `python -m pytest -q tests/test_orders_trades_table.py` and `python -m pytest -q`.
