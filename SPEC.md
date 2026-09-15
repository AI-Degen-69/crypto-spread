# SPEC.md — Issue #193: Stats Summary hero cards must reflect live oscillation data

Binding while `fix/summary-hero-live-data-193` is live. Supersedes the #191 contents.

## 1. Goal

The two hero cards at the top of the Stats Summary tab are hardcoded HTML
literals inside `FULL_APP_HTML`. They state research conclusions as fixed
numerals that no longer match what the dashboard itself computes from
`run/oscillation_summary.json`, and the gap widens on every collector run
because the denominator only grows.

Replace the numerals in the **Research Conclusion** card with values derived at
render time from the `/api/oscillation` payload, and label the **Recommended
Stop-Loss Thresholds** card honestly as a non-computed static claim so a reader
can tell at a glance which card is live and which is quoted.

Measured drift at planning time (`run/oscillation_summary.json`, ts `1789489501.5`):

| Claim | Hardcoded | Computed from the file |
| --- | --- | --- |
| Total windows measured | `2,820+` | `3,598` |
| Overall oscillating | `74%` | `74.2%` (2,669 / 3,598) |
| 5m oscillating | `73%` | `71.8%` (1,928 / 2,685) |
| 15m oscillating | `80%` | `81.2%` (741 / 913) |

## 2. Data source — no new endpoint

`/api/oscillation` (`server/osc_dash.py:325`) already returns `summary`, produced
by `load_summary()` (`server/osc_dash.py:215`). Each `summary.per_series[<slug>]`
entry carries every field needed:

```json
{"label": "BTC 5m", "duration": 300, "windows": 537, "oscillating": 400,
 "monotonic": 137, "flat": 0, "pair_cost_median": 1.01, "recent": [...]}
```

`duration` is `300` for 5m and `900` for 15m; only those two values occur in the
live file. `summary.ts` is a float epoch-seconds stamp (`1789489501.54343`).

When the file is absent or unparseable, `load_summary()` returns
`{"ts": 0, "per_series": {}}`. That is the empty case the UI must survive.

## 3. Interface contract (client-side JS, inside `FULL_APP_HTML`)

### 3.1 `computeOscillationHeadline(perSeries)` — pure, no DOM, no fetch

```js
/**
 * Aggregate per-series oscillation counts into headline figures.
 * @param {Object} perSeries - summary.per_series; may be {} / null / undefined.
 * @returns {{ok: boolean, totalWindows: number,
 *            overallPct: number|null, pct5m: number|null, pct15m: number|null,
 *            windows5m: number, windows15m: number}}
 */
```

Rules:
- Sum `windows` and `oscillating` over every entry; group by `duration === 300`
  (5m) and `duration === 900` (15m). Entries with any other `duration` count
  toward the totals but toward neither bucket.
- Percentages are `100 * oscillating / windows`, **not rounded** by this
  function — formatting is the caller's job.
- Any bucket whose `windows` is `0` yields `null` for that percentage, never
  `NaN`, never `0`. Same for `overallPct` when `totalWindows === 0`.
- `ok` is `true` only when `totalWindows > 0`.
- Missing numeric fields default to `0`; the function must not throw on a
  malformed entry.

### 3.2 `formatOscPct(value)` — pure

Returns `'—'` for `null`, `undefined`, `NaN`, or a non-finite number; otherwise
one decimal place plus a percent sign, e.g. `'74.2%'`.

### 3.3 `formatOscAsOf(ts)` — pure

Returns `'—'` when `ts` is falsy or `0`; otherwise
`new Date(ts * 1000).toLocaleString('en-US')`, matching the existing stamp
convention at `server/osc_dash.py:4427`.

### 3.4 `renderOscillationHero(summary)` — DOM writer

Reads `summary.per_series` and `summary.ts`, calls the three pure functions
above, and writes `textContent` into the element ids in §4. Must be safe to call
when an element is missing (guard every `$()` lookup), and must not throw when
`summary` is `null`/`undefined`.

### 3.5 Call site

`renderSummaryCharts()` (`server/osc_dash.py:4185`) already fetches
`/api/oscillation` into `d`. Call `renderOscillationHero(d.summary || {})`
immediately after that fetch, before the chart work, so one fetch feeds both.
No change to `switchTab()` (`server/osc_dash.py:3504`).

## 4. Element ids (locked)

| id | Content |
| --- | --- |
| `oscHeroOverallPct` | overall oscillating %, e.g. `74.2%` |
| `oscHeroTotalWindows` | total windows, thousands-separated, e.g. `3,598` |
| `oscHeroPct5m` | 5m oscillating % |
| `oscHeroPct15m` | 15m oscillating % |
| `oscHeroAsOf` | rendered `summary.ts` |
| `stopLossProvenance` | provenance line on the stop-loss card |

Every one of these ships in the static markup with the literal placeholder `—`,
so the card is honest before the first fetch resolves and in the empty-data case.

## 5. Acceptance criteria

- [ ] The literals `2,820+`, `74% of Windows Are Oscillating`, `73% oscillating`
      and `80% oscillating` do not appear anywhere in `server/osc_dash.py`.
- [ ] Opening the Stats Summary tab shows total windows, overall %, 5m % and
      15m % all matching values computed directly from
      `run/oscillation_summary.json`.
- [ ] The card shows an "as of" stamp derived from `summary.ts`.
- [ ] With `run/oscillation_summary.json` absent, the card renders `—` in every
      slot — no `NaN`, no misleading `0%` — and throws no console error.
- [ ] The stop-loss card visibly identifies itself as a non-computed static
      claim and names its provenance (see §6, pending operator decision).
- [ ] New tests in `tests/test_osc_dash_integration.py` assert the literals are
      gone and the ids are present; a Node-harness test exercises
      `computeOscillationHeadline` against populated and empty inputs.
- [ ] `python -m pytest -q tests/test_osc_dash_integration.py tests/test_orders_trades_table.py` passes.
- [ ] `python -m pytest -q` passes (full suite, ~390 tests).

## 6. Stop-loss card provenance — DECIDED: option A (operator, 2026-09-15)

The issue asks the stop-loss card to name "its source document and date". A
repo-wide search finds **no document that produces the numbers on that card**
(`BTC 5m +$0.09`, `SOL 5m +$0.11`, `ETH/BNB/XRP 5m +$0.12`, `15m +$0.13`).
`docs/operations.md:16,59` uses `0.09` only as a CLI usage example.

The newest EV research reaches the opposite conclusion:
`docs/ev-research-findings-2026-09-11.md:36` — "stop-loss exits are the largest
PnL destroyer in this dataset" — with a headline recommendation of `ex=none`
(hold to settlement, no stop-loss). That document carries its own superseded
banner pending issue #182.

**Locked: option A.** The card declares itself a static, non-computed heuristic
with no surviving sweep behind it, and points the reader at
`docs/ev-research-findings-2026-09-11.md` (2026-09-11) as the newest — itself
provisional — evidence that later work went the other way. The threshold numbers
themselves are not touched; recomputing them stays out of scope.

Rejected: B (cite only the divergence audit — does not disclose that the newer
research disagrees) and C (defer — leaves an acceptance criterion unmet).

## 7. Out of scope

- Recomputing the per-asset stop-loss thresholds from sweep data.
- Stale figures in prose docs (`README.md:9`, `README.md:14`, `docs/operations.md:99`).
- Any new API endpoint.
- The four charts below the hero, and the cockpit / backtest / ticks / analysis tabs.
- How `run/oscillation_summary.json` is produced (`scripts/rebuild_windows.py`).
