# SPEC — Issue #197: Interactive Column Header Sorting (ASC / DESC) Across All Cockpit Tabs

## Overview
Operators managing high-frequency 5m/15m crypto spread positions require the ability to interactively sort tables in the Live Cockpit (`#orders-trades-card`):
- Tab 1: Open Orders (`#cockpitOrdersTable`)
- Tab 2: Positions (`#cockpitPositionsTable`)
- Tab 3: Closed Trades (`#cockpitTradesTable`)

Sorting must be interactive, visual, type-aware, group-aware (retaining paired rows together), and persist seamlessly across 1-second live data updates.

---

## 1. Table Columns & Sort Keys

### Tab 1: Open Orders (`#cockpitOrdersTable`)
| Column | Sortable | Sort Data Type | Comparator Logic |
|---|---|---|---|
| Time | Yes | Chronological / Timestamp | `legs[0].time` (or group timestamp) |
| Market | Yes | Alphabetical | `grp.market` string compare |
| Side | Yes | Alphabetical / Priority | `legs[0].side` (e.g. Up vs Down) |
| Price | Yes | Numeric ($) | `legs[0].priceNum` (or pair cost) |
| Size | Yes | Numeric | `legs[0].sizeNum` |
| Filled | Yes | Numeric | `legs[0].filledNum` |
| Total Cost | Yes | Numeric ($) | Total cost of leg / group |
| Status | Yes | Alphabetical | `grp.status` ('Paired', 'Partial', 'Unpaired', 'Cancelled') |
| Action | **No** | N/A | Excluded from sorting |

### Tab 2: Positions (`#cockpitPositionsTable`)
| Column | Sortable | Sort Data Type | Comparator Logic |
|---|---|---|---|
| Time | Yes | Chronological / Timestamp | `legs[0].time` (or group timestamp) |
| Market | Yes | Alphabetical | `grp.market` string compare |
| Side | Yes | Alphabetical | `legs[0].side` |
| Size | Yes | Numeric | Total size or `legs[0].sizeNum` |
| Base Cost | Yes | Numeric ($) | `legs[0].baseCost` |
| Market Value | Yes | Numeric ($) | `grp.market_val` |
| Unrealized $ (%) | Yes | Numeric ($/%) | `grp.unrealized_usd` |
| Realized $ (%) | Yes | Numeric ($/%) | `grp.realized_usd` |

### Tab 3: Closed Trades (`#cockpitTradesTable`)
| Column | Sortable | Sort Data Type | Comparator Logic |
|---|---|---|---|
| Time | Yes | Chronological / Timestamp | `t.timestamp` |
| Market | Yes | Alphabetical | `t.label || t.market` |
| Cause | Yes | Alphabetical | `t.action` ('PAIR_MERGE', 'STOP', etc.) |
| Shares | Yes | Numeric | `t.shares` |
| Base Cost | Yes | Numeric ($) | Numeric entry price calculation |
| Exit Price | Yes | Numeric ($) | `t.exit_price` |
| Gain / Loss $ (%) | Yes | Numeric ($/%) | `t.pnl_usd` |
| Details | Yes | Alphabetical | `t.details` / outcome |

---

## 2. Behavioral Specifications

1. **Header Interaction**:
   - Clicking an unsorted column sets sort to `asc` (or `desc` for metrics like P&L/Time where descending is the natural inspection order).
   - Clicking an already-sorted column toggles direction: `asc` ↔ `desc`.
   - Optional 3rd click: resets to natural/default order (`none`).
   - Active column header displays a distinct visual indicator (`▲` for ASC, `▼` for DESC).
   - Sets accessible attribute `aria-sort="ascending"` or `aria-sort="descending"`. Inactive headers have `aria-sort="none"`.

2. **Group-Aware Sorting**:
   - Paired orders and positions are displayed as multi-row groupings with merged cells (`rowspan`).
   - The sorting engine sorts the group entities (`groupedOrders` keys / `groupedPos` keys) using group properties or lead-leg metrics before rendering the HTML table.
   - Rows within a pair remain intact and structurally ordered (e.g. UP before DOWN).

3. **Live Refresh Persistence**:
   - Sort state is maintained in memory (`otSortState[tab] = { col: key, dir: 'asc'|'desc' }`) and saved to `localStorage` (e.g. `crypto-spread-ot-sort`).
   - Whenever new cockpit data arrives via SSE or polling, `renderCockpitUI(st)` applies the active sort parameters before injecting HTML into the DOM.
