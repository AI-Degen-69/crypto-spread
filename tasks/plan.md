# Plan — Issue #226: one fill rule, and `fill_model` is deleted

**Size**: Large — 24 files, a public API contract (`/api/backtest`), a dashboard
control, a CLI flag, two engines and five research modules.
**Type**: Code (+ a Docs slice).
**Stack**: Python 3.12, FastAPI, pytest (932 tests / 18 files). Targeted tests only
locally; CI is the merge gate (`AGENTS.md`).
**Spec**: `SPEC.md`. **Gates**: `CONSTRAINTS.md`. **Rule of record**:
`docs/engine-decision-rules.md` §3 + `docs/adr/0002-single-hard-coded-fill-rule.md`.

## Interview

Not skipped, and it moved the rule. Reviewing the issue's fill-price table with the
operator (2026-09-16) established that its second row was wrong: an entry is a limit
order that waits, so it never pays a fee and never books the ask. `SPEC.md`,
`docs/engine-decision-rules.md` §3 and ADR-0002 were corrected before this plan was
finalised, and the issue body with them. Three things the issue text does not settle:

1. **Same-tick precedence** — moot. Both branches book the same price and no fee, so
   there is nothing to break a tie over.
2. **`research/sweeps/sim2.py` is in scope**, though the issue does not list it.
   `selection_bias.py` (live, tested) imports it, and `tests/test_ev_sweep_lab.py`
   scans its source for `p.<field>` exhaustiveness. It cannot stay on `fill_model`.

## Contract changes

```python
# backtest/engine.py
- fill_model: str = "tape"          # deleted from BacktestParams
- ("fill_model", "Fill Model", ...) # deleted from _PARAM_GROUPS["execution_assumptions"]

# new shared helper — the single fill decision (T1), in strategy/book_math.py
def resting_bid_filled(resting: float, best_ask: float | None,
                       tape_prices: Iterable[float], tick: float) -> bool:
    """Was our resting bid taken on this tick? Price is always `resting`; no fee."""
```

- `GET /api/backtest`: `fill_model` removed from the query contract and from the
  echoed `params` payload. An unknown query key is already ignored, so an old
  bookmark still runs — under the one rule.
- `scripts/backtest.py`: `--fill-model` removed.
- `scripts/replay_shadow_check.py`: legs collapse 8 → 4; leg keys lose their
  `tape_`/`book_` prefix; `verdict_leg` becomes `gates_pc1.05`.
- `BacktestParams.params_hash()` changes for every config (accepted, `SPEC.md`).

## Tasks

### T1 — `[Backend/Logic]` The single fill decision, in one place
**Files**: `strategy/book_math.py` (new `resting_bid_filled`), `backtest/engine.py`
(fill block ~1111-1160, field ~127, registry ~242, module docstring ~14).
**Do**: add the helper; delete the `fill_model` field, its registry entry and the
four-model branching; route the fill block through the helper. Fill price stays
`resting_*`, `entry_price_*` keeps latching to it, and **no fee is added** — the
entry paths do not call `_taker_fee` and the exit paths keep calling it exactly as
they do today.
**Do not**: touch the seven P&L sites (~1094, 1105, 1173, 1205, 1221, 1232, 1268).
They read `resting_*`, which is the fill price, which is correct. An earlier draft
of this plan rewrote them for a taker price that no longer exists.
**Skill**: `test-driven-development`, `source-driven-development`.
**Verify**: `python -m pytest tests/test_backtest_engine.py tests/test_book_math.py tests/test_docstrings.py -q`.

### T2 — `[Backend/Logic]` Live paper sim: fully through, still at our price
**Files**: `strategy/live_trader.py` — three sim sites (~5034, ~5068, ~5104), the
tape-tolerance comment at ~2079, and a comment at the chase site (~5055) recording
the marketable-limit case (`SPEC.md`).
**Do**: `ask <= resting` becomes the `resting_bid_filled` call. `fill_price_*` stays
`resting_*`. `_try_ws_tape_fill` is unchanged. The live engine computes no fees at
all today and still does not.
**Skill**: `test-driven-development`.
**Verify**: `python -m pytest tests/test_live_trader.py tests/test_fill_telemetry.py tests/test_entry_timeout.py -q`.

### T3 — `[API/Dashboard]` Drop the knob from the surface
**Files**: `server/osc_dash.py` (param ~631, ~706, ~780, ~997; `<select>` ~2521; the
"Verify fill_model" warning ~2596 + ~4125; the query string ~4083),
`tests/test_param_registry.py`, `tests/test_osc_dash_integration.py`.
**Do**: delete the parameter, the control and the surrounding "these describe how we
assume the book fills you" copy; reword the zero-fills warning to point at tape data
density alone.
**Skill**: `frontend-ui-engineering` (the panel must not be left with a hole),
`api-and-interface-design`.
**Verify**: `python -m pytest tests/test_osc_dash_integration.py tests/test_param_registry.py -q`,
then a browser check of the Backtest tab (control gone, a run still returns fills).

### T4 — `[Backend/CLI]` Scripts
**Files**: `scripts/backtest.py`, `scripts/sweep_backtest.py`,
`scripts/replay_shadow_check.py`.
**Do**: remove `--fill-model` and every `fill_model=` pass-through; collapse the
shadow-check legs to `gates × pair_cap` and rename `verdict_leg`.
**Verify**: `python -m pytest tests/test_backtest_cli.py tests/test_sweep_backtest.py tests/test_replay_shadow_check.py -q`.

### T5 — `[Research/Logic]` The live research simulators
**Files**: `research/sweeps/ev_lab.py` (fill block ~515-600, base ~926, ~1156-1157),
`research/sweeps/sim2.py` (~93, ~239-290), `research/sweeps/selection_bias.py` (~72).
**Do**: one fill path, same helper; delete the `tapeq` queue state and its
`q_up`/`q_dn`/`q_rest_*` locals; drop the `fill_model="book"`/`"cross"` comparison
configs.
**Verify**: `python -m pytest tests/test_ev_sweep_lab.py tests/test_selection_bias.py -q`.

### T6 — `[Chore]` Delete the frozen sweep drivers
**Delete**: `research/sweeps/phase1_1d.py`, `phase2_fills.py`, `phase3_mech.py`,
`phase4_universe.py`, `phase5_band.py`, `phase6_tapeq_top.py`, `validate_top.py`,
`run_exit_rev_110.py`.
**Keep**: every `*.json` in that directory — `docs/ev-research-findings-2026-09-11.md`
cites seven of them as the evidence behind `patient_band_maker`.
**Also touch**: `tests/test_ev_sweep_lab.py` (drop
`test_phase1_baseline_matches_the_documented_one`, which dies with `phase1_1d.py`;
the `SWEEPS.glob("*.py")` guards at :105, :164, :267, :318, :578, :775 need no edit
— they simply scan fewer files), `research/sweeps/RESULTS-ARE-STALE.md` and
`research/sweeps/README.md` (record that the drivers are gone and the tables are a
historical record only), `docs/ev-research-findings-2026-09-11.md:244` (the
"reproduce any row: `python research/sweeps/phase5_band.py`" line is now false —
and was already false per `RESULTS-ARE-STALE.md`).
**Why deletion and not a patch**: nothing imports them; they cannot run against the
current engine; keeping them alive only to carry a keyword this issue deletes is the
tail wagging the dog. Operator decision, 2026-09-16. Git history keeps them.
**Verify**: `python -m pytest tests/test_ev_sweep_lab.py -q`; then
`grep -rn "phase1_1d\|phase5_band\|validate_top\|run_exit_rev_110" --include=*.py .`
returns nothing outside the `.json` names.

### T7 — `[Test/Parity]` The parity test the issue asks for
**Files**: new `tests/test_fill_rule_parity.py`, reusing the `_snap` / `_drive_live`
plumbing from `tests/test_entry_anchor_parity.py` (`tests/` is a package).
**Do**: one shared snapshot sequence per scenario, both engines, assert identical
fills **and** fill prices: (a) tape print at our price → both fill at `R`; (b) ask
fully through → both fill at `R`, not at the ask; (c) ask *equal* to our price →
neither fills; (d) either branch → `fees_cents` is unchanged by the entry.
**Verify**: `python -m pytest tests/test_fill_rule_parity.py -q`.

### T8 — `[Docs]` The surfaces that still describe a knob
**Files**: `AGENTS.md:49` (the `/api/backtest` query list), `docs/operations.md:66,
74, 109`, `docs/backtest-optimization-results.md:4, 90` (a header note that the
numbers predate the rule — the numbers themselves stay).
**Do**: nothing new is decided here. `docs/engine-decision-rules.md` §3 and ADR-0002
were corrected up front (see Interview) and are the definition; these files only stop
pointing at a knob that no longer exists.

## Order and commits

T1 → T2 → T7 (parity proves T1-T2) → T3 → T4 → T5 → T6 → T8.
One atomic commit per task, conventional, scoped (`fix(backtest):`,
`fix(strategy):`, `fix(dash):`, `chore(research):`, `docs:`). Branch
`fix/one-fill-rule-226` off `master`.

## Improvement proposed (operator decides — not folded in silently)

**Make parity structural, not just asserted.** The issue asks for a parity *test*.
A test proves the two engines agree today; a shared `resting_bid_filled` helper (T1) makes
them unable to disagree tomorrow, and deletes the third and fourth copies of the
predicate in `ev_lab.py` and `sim2.py` at the same time. It is the reason four
"models" could drift apart in the first place. Cost: one small function and an
import from `strategy/` into `backtest/` — which `backtest/engine.py` already does
(`from strategy import book_math`), so `strategy/book_math.py` is its natural home.
**Adopt / defer / drop?** The plan above assumes *adopt*; dropping it means T1
keeps the logic inline in each engine and T8 carries the whole guarantee.
