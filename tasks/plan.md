# Plan — Issue #155: Redesign Recent Windows table (entry-relative MAX UP/DOWN, RESULT pill, hyperlink SERIES cell)

- **Issue:** https://github.com/AI-Degen-69/crypto-spread/issues/155
- **Branch:** `feat/recent-windows-redesign-155` (off `master`)
- **Size tier:** **Small** — 1 application file (`server/osc_dash.py`, Recent Windows render block ~lines 3817–3838) + 1 new test file (`tests/test_recent_windows_table.py`). No schema change, no new endpoint, no new dependency; all record fields (`start_mid`, `close_mid`, `min_mid`, `max_mid`, `start_ts`, `end_ts`, `url`) already exist on the window record (`scripts/measure_5m_oscillation.py:241-260`, `scripts/rebuild_windows.py:79-127`).
- **Task type:** **Design/UI** (table columns, pills, hyperlinks, highlight states) + **Code** (JS render logic inside `FULL_APP_HTML`, HTML-string regression tests).
- **Stack detected:** Python 3.12 / FastAPI (`server/osc_dash.py`), embedded SPA in `FULL_APP_HTML`, Chart.js already loaded; tests via `pytest` + `fastapi.testclient` HTML-string assertions (pattern: `tests/test_orders_trades_table.py`).
- **Verification mode:** Automated tests (`python -m pytest tests/test_recent_windows_table.py tests/test_osc_dash_integration.py -q`, then full `python -m pytest -q`). The table renders client-side in JS, so tests assert on JS-source fragments inside `GET /` HTML.
- **interview-me:** Skipped. Requirements were fully clear from the issue — explicit formulas (`max_mid - start_mid`, `start_mid - min_mid`, `close_mid >= 0.50`), explicit column add/drop list, explicit files, explicit acceptance criteria.
- **Line-number drift note:** The issue cites `server/osc_dash.py:3398/3415/3273`, but the current file renders the Recent Windows table at ~lines 3817–3838 and `marketName()` at line 3594. Tasks below use actual locations.

## Skills prescribed per task

| Domain | Skill |
| --- | --- |
| Planning | `spec-driven-development`, `constraint-driven-development`, `api-and-interface-design`, `planning-and-task-breakdown` |
| UI & Layout | `frontend-ui-engineering` (project tokens only: `var(--up)`/`var(--down)`, `price-up`/`price-down`, `pill()` styles, `tabular-nums`; text labels with every color signal) |
| Build & Quality | `test-driven-development`, `code-review-and-quality` |

---

## Concise spec (embedded — Small tier, no SPEC.md rewrite)

**Goal:** MAX UP/DOWN answer the trader's question ("how far from my OPEN entry — did it reach the 5¢ exit?") instead of re-rendering the candle high/low against the fixed 0.50 base; resolution becomes visible via RESULT; SERIES cell absorbs the link.

**Contracts (locked before build):**
- C1 — Header: `Series | Open UP/DOWN | Max UP | Max DOWN | Candle | Class | Result` (7 columns; `Window` and `Link` headers deleted).
- C2 — MAX UP = `max_mid - start_mid`, MAX DOWN = `start_mid - min_mid`, rendered with `fmtPrice()` as `+$0.XX`; any null input renders `-` (never `NaN`); highlight emphasis (existing `price-up`/`price-down`) only when delta ≥ 0.05.
- C3 — RESULT: `close_mid == null` → `-`; `close_mid >= 0.50` → green UP pill; else red DOWN pill. Reuses `pill()` helper styling; text label always present (never color alone).
- C4 — SERIES cell: `marketName(series)` (`BTC 15m`) + small-font 24h `HH:MM-HH:MM` range derived from `start_ts`/`end_ts`; the whole cell is `<a href="url" target="_blank" rel="noopener">` with URL through `esc()`. Slug-hash text and standalone `Open ↗` link cell deleted.
- C5 — OPEN UP/DOWN, CANDLE, CLASS byte-for-byte behavior unchanged (row keeps column order otherwise).

**Edge cases:** null `start_mid`/`max_mid`/`min_mid` → `-`; null `close_mid` → `-`; missing `url` → existing `esc(w.url||'#')` fallback preserved; `start_ts`/`end_ts` missing → `-` range (current `startTs` fallback pattern).

**Out of scope:** window record schema, `classify_window`, backtest, collector, any non-dashboard code.

---

## Tasks

### T1 — [x] `[Design/UI]` Header + SERIES cell: hyperlink + time range, drop WINDOW/LINK columns
- **Domain:** `[Design/UI]`
- **Target Files:** `server/osc_dash.py` (~lines 3818, 3835)
- **Skill:** `frontend-ui-engineering`
- **Description:**
  - Rewrite the `<thead>` row to the 7-column C1 contract (delete `<th>Window</th>` and `<th>Link</th>`, add `<th>Result</th>`).
  - Rewrite the SERIES `<td>`: `<a>` wrapping `marketName(w.series||w.label||'')` + `<div>` small-font `HH:MM-HH:MM` range from `start_ts`/`end_ts` (24h, `toLocaleTimeString`-style as today); delete the slug-hash text cell and the trailing `Open ↗` link cell.
- **Verification:** New tests in `tests/test_recent_windows_table.py` asserting the 7-column header exists in `GET /` HTML and `<th>Window</th>` / `<th>Link</th>` / `Open ↗` row-link cell are gone.

### T2 — [x] `[Design/UI]` Entry-relative MAX UP/DOWN + ≥$0.05 highlight
- **Domain:** `[Design/UI]`
- **Target Files:** `server/osc_dash.py` (~lines 3820–3826, 3835)
- **Skill:** `frontend-ui-engineering`
- **Description:**
  - Replace `w.max_up`/`w.max_down` (0.50-based) excursion with entry-relative deltas per C2 (`mx-sm`, `sm-mn`), keeping `upHigh`/`downHigh` absolute price display.
  - Apply highlight emphasis only at delta ≥ 0.05; null-safe (`-` on any null input).
- **Verification:** Tests asserting the render source contains the entry-relative computation and the `0.05` threshold, plus a pure-JS-threshold check via Node if available (else string-assert); manual check: 0.54-open/0.55-high fixture shows +$0.01.

### T3 — [x] `[Design/UI]` RESULT pill column
- **Domain:** `[Design/UI]`
- **Target Files:** `server/osc_dash.py` (~line 3835)
- **Skill:** `frontend-ui-engineering`
- **Description:**
  - Add the RESULT `<td>` per C3 (green UP / red DOWN / `-`), reusing the `pill()` helper classes; place after Class cell.
- **Verification:** Tests asserting UP/DOWN/`-` pill logic fragments exist in `GET /` HTML.

### T4 — [x] `[Code]` Regression test file + full-suite gate
- **Domain:** `[Code]`
- **Target Files:** `tests/test_recent_windows_table.py`
- **Skill:** `test-driven-development`
- **Description:**
  - Create `tests/test_recent_windows_table.py` following the `test_orders_trades_table.py` pattern (`TestClient GET /`, HTML-string assertions) covering C1–C4 acceptance criteria.
  - Run targeted gate, then `tests/test_osc_dash_integration.py`, then full `python -m pytest -q` — 0 failures.
- **Verification:** `python -m pytest tests/test_recent_windows_table.py tests/test_osc_dash_integration.py -q` green, then full suite green.
