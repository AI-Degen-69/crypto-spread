# Plan — issue #193: Stats Summary hero cards reflect live oscillation data

- **Issue:** https://github.com/AI-Degen-69/crypto-spread/issues/193
- **Branch:** `fix/summary-hero-live-data-193` (off `master`)
- **Size tier:** **Standard** — 2 files (`server/osc_dash.py`,
  `tests/test_osc_dash_integration.py`, plus the Node harness in
  `tests/test_orders_trades_table.py`), internal module change, one
  architectural decision (pure-aggregate + DOM-writer split). Not Small: it
  spans markup, JS logic and two test harnesses. Not Large: no new dependency,
  no public API change, no schema change.
- **Task type:** **Debug/Correctness** (the UI states false numbers) +
  **Design/UI** (hero card markup) + **Backend-adjacent read-only** (payload
  already exists; no server change).
- **Stack detected:** Python 3.12.10 / FastAPI, served SPA in `FULL_APP_HTML`;
  tests `pytest` (898 tests, ~82s) plus a Node v24.14.1 DOM/eval harness used
  by `tests/test_orders_trades_table.py:118`.
- **Verification mode:** automated tests only (pytest + Node harness). No
  browser preview step required — the assertions cover markup, pure logic and
  the empty-data path.
- **interview-me:** skipped. The issue carries file:line anchors, the exact data
  shape, the empty-data contract and eight acceptance criteria. One genuine
  ambiguity exists (stop-loss card provenance) and is raised as a decision in
  the Improvement pass below rather than guessed at.

## Skills prescribed per task

| Domain | Skill |
| --- | --- |
| Planning | `spec-driven-development`, `constraint-driven-development`, `api-and-interface-design`, `planning-and-task-breakdown` |
| Build | `test-driven-development` (red then green, per task), `incremental-implementation` |
| UI slice | `frontend-ui-engineering` (placeholder/empty state, provenance labelling) |
| Correctness | `debugging-and-error-recovery` (the `NaN` / `0%` defect class) |

---

## T1 — [x] `[Debug/Logic]` Pure aggregation function, test-first

- **Files:** `tests/test_orders_trades_table.py` (new Node-harness test),
  `server/osc_dash.py` (JS inside `FULL_APP_HTML`)
- **Skill:** `test-driven-development`
- **Red:** add `test_compute_oscillation_headline_js`, modelled on
  `tests/test_orders_trades_table.py:118` — extract the served `<script>`, eval
  it in Node with the existing mocked globals, then call
  `computeOscillationHeadline` directly. Cases:
  1. two 5m series plus one 15m series produce the exact `totalWindows`, and
     percentages within `1e-9` of hand-computed values;
  2. `{}` produces `ok === false`, all three percentages `null`,
     `totalWindows === 0`;
  3. a series with `windows: 0` produces `null` for that bucket, not `NaN`;
  4. an entry missing `oscillating` is treated as `0` and does not throw.
- **Green:** implement `computeOscillationHeadline(perSeries)` per `SPEC.md`
  section 3.1, next to the other JS helpers. Pure: no DOM, no fetch.
- **Verify:** `python -m pytest -q tests/test_orders_trades_table.py -k oscillation_headline`

## T2 — [x] `[Debug/Logic]` Formatters, test-first

- **Files:** same two
- **Skill:** `test-driven-development`
- **Red:** extend the T1 harness test with `formatOscPct` and `formatOscAsOf`:
  `formatOscPct(74.1666)` gives `'74.2%'`; `formatOscPct(null)`,
  `formatOscPct(NaN)` and `formatOscPct(Infinity)` each give the em-dash;
  `formatOscAsOf(0)` gives the em-dash; `formatOscAsOf(1789489501.54)` gives a
  non-dash string containing the year.
- **Green:** implement both per `SPEC.md` sections 3.2 and 3.3.
- **Verify:** same command as T1.

## T3 — [x] `[Design/UI]` Replace the Research Conclusion card markup

- **Files:** `server/osc_dash.py:2612-2619`
- **Skill:** `frontend-ui-engineering`
- **Do:** swap the four hardcoded numerals for the five spans of `SPEC.md`
  section 4, each shipping the em-dash placeholder. Keep the card's existing
  visual treatment (`border-top:2px solid var(--up)`, the 24px mono headline,
  the 12.5px dim body). Add the "as of" stamp as a small dim line under the body
  copy. The explanatory sentence about `mid - offset` quoting stays — it is
  mechanism, not a measured number.
- **Red first:** add `test_summary_hero_literals_removed` and
  `test_summary_hero_element_ids_present` to
  `tests/test_osc_dash_integration.py`, modelled on
  `tests/test_osc_dash_integration.py:717`.
- **Verify:** `python -m pytest -q tests/test_osc_dash_integration.py -k summary_hero`

## T4 — [x] `[Design/UI]` Wire the renderer into the existing fetch

- **Files:** `server/osc_dash.py:4185-4187`
- **Skill:** `incremental-implementation`
- **Do:** implement `renderOscillationHero(summary)` per `SPEC.md` section 3.4
  (guarded `$()` lookups, `textContent` writes only) and call it from
  `renderSummaryCharts()` immediately after the `/api/oscillation` fetch, reusing
  `d`. No second fetch. No change to `switchTab()`.
- **Red first:** a Node DOM test asserting that with a populated summary the five
  ids receive non-dash text, and that with `{ts: 0, per_series: {}}` every one of
  them reads the em-dash and nothing throws.
- **Verify:** `python -m pytest -q tests/test_orders_trades_table.py -k oscillation_hero`

## T5 — [x] `[Design/UI]` Label the stop-loss card's provenance

- **Files:** `server/osc_dash.py:2620-2629`
- **Skill:** `frontend-ui-engineering`
- **Decision:** option A, locked by the operator on 2026-09-15 (`SPEC.md` section 6).
- **Do:** add the `stopLossProvenance` line to the card, stating plainly what it
  is: an unsourced static heuristic, with `docs/ev-research-findings-2026-09-11.md`
  (2026-09-11) named as the newest — itself provisional — evidence that later
  work reached the opposite conclusion. The threshold numbers themselves are
  untouched; recomputing them is out of scope.
- **Red first:** `test_stoploss_card_declares_static_provenance`, asserting the
  id exists and the card carries both a "not computed" marker and a document
  reference.
- **Verify:** `python -m pytest -q tests/test_osc_dash_integration.py -k stoploss`

## T6 — [x] `[Gate]` Full verification and PR prep

- **Do:** run the targeted gate, then the full suite; confirm no `run/` file is
  staged; confirm the committed tree — not just the working tree — is what was
  tested, via `git show HEAD:server/osc_dash.py | diff - server/osc_dash.py`.
- **Verify:**
  1. `python -m pytest -q tests/test_osc_dash_integration.py tests/test_orders_trades_table.py`
  2. `python -m pytest -q`
- **Then:** hand off to Station IV (`iv-review-build-and-pr`).

---

## Improvement pass — ADOPTED (option A)

**Finding:** the issue instructs T5 to label the stop-loss card with "its source
document and date". There is no such document. A repo-wide search turns up no
file that produces `+$0.09 / +$0.11 / +$0.12 / +$0.13`; `docs/operations.md:16`
and `docs/operations.md:59` use `0.09` only as a CLI usage example.

The newest evidence points the other way:
`docs/ev-research-findings-2026-09-11.md:36` records that stop-loss exits are the
largest PnL destroyer in that dataset, and its headline recommendation is
`ex=none` — hold to settlement, no stop-loss at all. That document carries its
own superseded banner pending issue #182.

So the card is not merely stale like its neighbour: it is a recommendation the
repo's own later research contradicts. Citing a source it does not have would
make it look *more* trustworthy than it is — the opposite of the issue's intent.

**Proposal (option A in `SPEC.md` section 6):** label it as an unsourced legacy
heuristic and point the reader at `docs/ev-research-findings-2026-09-11.md` as
the newest, itself-provisional evidence that later work disagreed. Cost: one
extra markup line, no logic. Alternatives B (cite only the divergence audit at
`docs/research-spread-bot-conclusions.md:74`) and C (defer to a new issue) are
written out in `SPEC.md` section 6.

**Adopted.** Operator chose A on 2026-09-15.
