# Spec: Dashboard LTR Layout & English Localization (Issue #61)

## 1. Objective
Refactor the Crypto Spread single-page dashboard (`server/osc_dash.py`) from Right-to-Left (`dir="rtl"`, `lang="he"`) to Left-to-Right (`dir="ltr"`, `lang="en"`):
1. **Document Flow**: Switch root document direction to standard LTR and English language.
2. **Alignment Consistency**: Align section headers, titles, primary identifiers, table column headers (`.tbl th`), and form labels leftwards; align supplementary metrics, badges, countdown timers, and control actions rightwards.
3. **Cockpit Layout**: Ensure the Live Trading Cockpit header group sits on the left and action buttons on the right; organize parameter configuration inputs to read naturally LTR across a 4-column responsive grid; keep filter chips and duration buttons naturally ordered.
4. **Complete English Localization**: Standardize all 138 legacy Hebrew strings across the Cockpit, Live Books tab, Backtest Sweeper, Stats Summary, Tick Files manager, upload dropzone, verification modals, and confirmation dialogs to clear, professional English.

---

## 2. Tech Stack
- **Backend & Serving**: Python 3.11+ (`fastapi`, `uvicorn`, `requests`)
- **Frontend Architecture**: Single-file SPA embedded in `server/osc_dash.py` (`FULL_APP_HTML`)
- **Styles & Scripts**: Vanilla CSS3 (Flexbox & Grid, CSS variables), Vanilla ES6 JavaScript, Chart.js 4.4.0
- **Testing**: `pytest`, `pytest-asyncio`, FastAPI `TestClient`

---

## 3. Commands
- Run all project tests: `python -m pytest -q`
- Run dashboard integration tests: `python -m pytest tests/test_osc_dash_integration.py -q`
- Start dashboard server: `python -m uvicorn server.osc_dash:app --host 127.0.0.1 --port 8802`
- Verify no remaining Hebrew characters:
  ```powershell
  $env:PYTHONIOENCODING='utf-8'; python -c "import re; lines = [i for i, l in enumerate(open('server/osc_dash.py', encoding='utf-8'), 1) if re.search(r'[\u0590-\u05ff]', l)]; print(f'Hebrew lines remaining: {len(lines)}')"
  ```

---

## 4. Project Structure
```
server/
  osc_dash.py                  -> Source of truth for SPA HTML, CSS, JS, and API endpoints
tests/
  test_osc_dash_integration.py -> Integration test verifying root HTML, LTR attributes, and English headers
  test_orders_trades_table.py  -> Unit tests for Cockpit tables & components
SPEC.md                        -> This specification document
```

---

## 5. UI Layout & Component Contracts

### 5.1 Root Document & Global CSS
- **Root Tag**: `<html lang="en" dir="ltr">`.
- **Table Headers (`.tbl th`)**: Change `text-align: right` to `text-align: left`.
- **Form Labels (`.form-group label`)**: Ensure `text-align: left`.
- **Monospace Helper (`.mono`)**: Remove legacy `direction:ltr;unicode-bidi:isolate` workaround since the parent document is native LTR.

### 5.2 Live Trading Cockpit Header & Control Bar
- **Container**: `display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; margin-bottom: 12px`.
- **Left Group**:
  - Title: `⚡ Live Trading Cockpit (5m Markets)`
  - Status Pills: `#cockpitStatusPill` (`BOT: STOPPED`), `#cockpitModePill` (`PAPER TRADING`), `#cockpitStreamPill` (`📡 STREAM: ...`)
- **Right Group**:
  - `#btnCockpitToggle` (`▶ START BOT`)
  - Restart button (`🔄 RESTART`)
  - Demo Data button (`🎲 DEMO DATA`)
  - `#btnSyncRealRun` (`📥 Sync Real Run (Polymarket)`)
  - Reset P&L button (`🗑 RESET P&L`)
  - `#btnPanicCancel` (`🚨 PANIC CANCEL ALL`)

### 5.3 Cockpit Parameter Form Grid (`.form-grid`)
- Standard 4-column CSS grid (`grid-template-columns: repeat(4, 1fr)`).
- **Row 1 (4 Columns)**:
  1. `Spread Offset (Rest @ 0.50 - offset)` (`#cockpitOffset`, default `0.02`)
  2. `Exit Stop Loss Threshold ($)` (`#cockpitExit`, default `0.05`)
  3. `Share Size (per leg)` (`#cockpitShares`, default `5`)
  4. `Execution Mode` (`#cockpitMode`: Paper vs Live)
- **Row 2 (4 Columns)**:
  1. `Polymarket Wallet Address (Optional)` (`#cockpitWallet`, style: `grid-column: span 2` to accommodate full `0x...` hex strings)
  2. `Starting Portfolio Balance ($)` (`#cockpitStartBal`, 1 column)
  3. Action Column: `#btnApplyParams` (`💾 APPLY PARAMETERS`, 1 column, right-aligned)

### 5.4 Market Filter Bar
- Container: Flex `justify-content: space-between`.
- **Left**: `Assets:` label, token chips (`BTC`, `ETH`, `BNB`, `SOL`, `XRP`), `All`, `Clear`, and `#cockpitFilterLockHint`.
- **Right**: `Duration:` label, duration chips (`5m`, `15m`, `Both`).

### 5.5 Live Market Matrix & Cards
- **Matrix Header**: `🎯 Live Market Matrix` left-aligned, active markets count badge (`#cockpitActiveMarketsBadge`) right-aligned.
- **Card Flow**: Left-to-right (`BTC 5m` -> `ETH 5m` -> `BNB 5m` -> `SOL 5m` -> `XRP 5m`).
- **Internal Card Flow**:
  - Header: Token label + indicator dot left-aligned, countdown timer (`⏱ 0m 00s`) right-aligned.
  - Price row: Mid price left-aligned, touch spread right-aligned.
  - Book depth: Bids/Asks for UP and DOWN left-aligned.
  - Spot row: Spot 1s price left-aligned, drift % right-aligned.
  - Position row: Orders & Position status left-aligned.
  - Footer: Status pill badge left-aligned, PnL amount right-aligned.

### 5.6 Comprehensive Localization Dictionary
All legacy Hebrew strings are translated into concise, professional English:

| Context / Element | Previous (Hebrew) | Standardized English |
|---|---|---|
| Top Header Status | `קולקטור: טוען...` | `Collector: Loading...` |
| Top Header Tape | `Tape: טוען...` | `Tape: Loading...` |
| Top Header Polling Btn | `הפעל איסוף רציף (1s)` / `עצור איסוף` | `Start Polling (1s)` / `Stop Polling` |
| Top Header Sample Btn | `דגום עכשיו (Once)` | `Poll Now (Once)` |
| Cockpit Sync Button | `📥 סנכרן ריצה אמיתית (Polymarket)` | `📥 Sync Real Run (Polymarket)` |
| Cockpit Sync State | `⏳ מסנכרן מפולימרקט...` | `⏳ Syncing from Polymarket...` |
| Live Tab Goals | `🎯 Goal Count — יעדים לספירת חלונות` | `🎯 Window Capture Targets` |
| Live Tab Windows | `חלונות חיים עכשיו — Live Books & Queue` | `Live Windows — Books & Queue` |
| Live Tab Recent | `חלונות אחרונים — פתיחה 50/50` | `Recent Windows — 50/50 Open (Click for chart)` |
| Backtest Header | `⚡ הגדרות פרמטרים לבקטסט` | `⚡ Backtest Parameters` |
| Backtest Tick File | `קובץ דגימות לבדיקה` | `Tick File Dataset` |
| Backtest File Default | `כל הקבצים / 2,820 חלונות (ברירת מחדל)` | `All Files / 2,820 Windows (Default)` |
| Backtest Fill Model | `Cross (חצייה מלאה ≤47¢ — מובטח)` | `Cross (Guaranteed if crossing ≤47¢)` |
| Backtest Run Button | `הרץ סימולציה (Run Sweep)` | `Run Sweep` |
| Backtest Reset Button | `איפוס לברירת מחדל` | `Reset to Defaults` |
| Backtest KPI Row | `רווח/הפסד כולל`, `אחוז לכידת זוג`, `Max Drawdown` | `Total P&L`, `Pair Capture Rate`, `Max Drawdown` |
| Stats Summary Hero | `מסקנת המחקר — SPREAD-2` | `Research Conclusion — SPREAD-2` |
| Stats Summary Callouts | `המלצות רף יציאה`, `רף +$0.09` | `Recommended Stop-Loss Thresholds`, `Stop +$0.09` |
| Stats Summary Charts | `1. אחוז תנודתיות לפי נכס`, `2. התפלגות טווח תנועה` | `1. Oscillation Rate by Asset`, `2. Max Excursion Distribution` |
| Tick Repo Header | `💾 קובצי Ticks בשרת` | `💾 Tick Data Files (JSONL Repository)` |
| Tick Verify All Btn | `🔍 בדיקת תקינות מלאה (Verify All)` | `🔍 Verify All Files` |
| Tick Refresh Btn | `🔄 רענן רשימה` | `🔄 Refresh List` |
| Tick Dropzone Prompt | `גרור לכאן קובץ .jsonl או לחץ לבחירה` | `Drag & drop a .jsonl file here or click to browse` |
| Tick Dropzone Info | `תומך בהעלאת קבצי ענק (10MB–1GB+) בהזרמה ישירה` | `Supports streaming upload of large files (10MB–1GB+) with zero memory buffering` |
| Tick Dropzone Select | `📁 בחר קובץ מהמחשב` | `📁 Select File from Computer` |
| Delete Modal Title | `אישור מחיקת קובץ` | `Confirm File Deletion` |
| Delete Modal Prompt | `האם אתה בטוח שברצונך למחוק לצמיתות את הקובץ:` | `Are you sure you want to permanently delete the file:` |
| Delete Modal Confirm | `כן, מחק קובץ` | `Yes, Delete File` |
| Delete Modal Cancel | `ביטול` | `Cancel` |
| Verify Modal Title | `דוח תקינות נתונים (Tick Integrity Report)` | `Tick Data Integrity Report` |
| Verify Modal Close | `✖ סגור` | `✖ Close` |
| Verify Summary Stats | `סטטוס תקינות כללי`, `דגימות תקינות`, `שורות פגומות` | `Overall Integrity Status`, `Valid Samples`, `Corrupt Rows` |

---

## 6. Code Style & Conventions
- Maintain single-file embedded structure in `server/osc_dash.py`.
- Preserve existing element IDs and event handler bindings (`onclick`, `onchange`).
- Do not introduce external frontend frameworks; keep HTML/CSS/JS lean and standard.
- Follow Python PEP 8 formatting standards in `server/osc_dash.py`.

---

## 7. Testing Strategy
1. **Automated Integration Tests (`tests/test_osc_dash_integration.py`)**:
   - Assert root page `<html lang="en" dir="ltr">`.
   - Assert absence of `dir="rtl"` and `lang="he"`.
   - Assert `.tbl th` contains `text-align:left` or defaults to left.
   - Assert Cockpit and Backtest headers appear in English.
   - Assert zero Hebrew characters exist in `server/osc_dash.py`.
2. **Regression Testing**:
   - Run complete test suite (`python -m pytest -q`) to ensure all 193 existing tests continue to pass.
3. **Manual Verification**:
   - Start dashboard on port 8802 and verify layout in browser across desktop and mobile breakpoints.

---

## 8. Boundaries
- **Always do:**
  - Preserve all element IDs (`btnCockpitToggle`, `btnSyncRealRun`, `cockpitOffset`, `chip-token-BTC`, etc.) so that live WebSocket and polling scripts work without interruption.
  - Verify every single Hebrew character in `server/osc_dash.py` is translated to English.
  - Run `python -m pytest -q` and verify all tests pass.
- **Ask first:**
  - Changing API endpoint contracts or data payloads.
  - Removing any existing card, chart, or telemetry feature.
- **Never do:**
  - Alter the underlying trading engine or strategy logic in `strategy/live_trader.py`.
  - Re-introduce `dir="rtl"` or mixed directional CSS overrides.

---

## 9. Success Criteria
- [ ] Root HTML tag specifies `lang="en"` and `dir="ltr"`.
- [ ] Section headers and card titles align to the left; action buttons and badges align to the right.
- [ ] Table column headers (`.tbl th`) and form labels align to the left across all tabs.
- [ ] Cockpit parameter form grid flows naturally LTR, with Wallet Address spanning 2 columns and Apply button on the right.
- [ ] All 138 lines containing Hebrew characters in `server/osc_dash.py` are converted to English.
- [ ] Zero Hebrew characters remain in `server/osc_dash.py`.
- [ ] All tests pass (`python -m pytest -q`) with zero regressions.

---

## 10. Open Questions
- None. Requirements and scope are fully specified in Issue #61.
