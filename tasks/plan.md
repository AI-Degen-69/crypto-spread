# Plan — Issue #227: `max_pair_cost` caps the chase only

**Size**: Large — 18 files, a public API contract (`/api/backtest`), a dashboard
control and its toggle, a CLI flag, both engines, two research simulators and eight
test files. One behaviour deletion and one field rename that changes `params_hash()`.
**Type**: Code (+ a Docs slice).
**Stack**: Python 3.12, FastAPI, pytest (971 tests / 19 files). Targeted tests only
locally; CI is the merge gate (`AGENTS.md`).
**Spec**: `SPEC.md`. **Gates**: `CONSTRAINTS.md`. **Rule of record**:
`docs/engine-decision-rules.md` §4 + `docs/adr/0003-structural-limits-separate-from-tuning-knobs.md`.

## Interview

Skipped — the issue settles every question it raises. The rule text, the formula, the
default, the maximum, the rename and the rejected alternative (checking the book's two
asks) are all written down and agreed with the operator on 2026-09-16. Three things the
issue does not name explicitly, resolved from the code and recorded in `SPEC.md`:

1. **The research simulators carry the same entry-side test**, in the book-ask form
   the issue rejects (`ev_lab.py:486`, `sim2.py:177`). They are in scope.
2. **The dashboard has an ON/OFF toggle** for the Backtest pair-cost field that sets
   the value to `0.0` to disable the gate (`osc_dash.py:2411-2418, 4034-4035`). It
   switched the gate being deleted, so it goes with it.
3. **`strategy/config.py:649` is a different bot's knob** with the same name. Out of
   scope, recorded in `SPEC.md`.

## Contract changes

```python
# backtest/engine.py
- pair_cost_gate: float = 1.05
+ max_pair_cost: float = 0.99          # validated to [0.50, 1.00] in __post_init__

- ("pair_cost_gate", "Max Pair Cost ($)", "Your cost threshold before walking away",
-  "$", (0.0, 2.0), ("backtest", "cockpit"), {"cockpit": (0.50, 1.00)})
+ ("max_pair_cost", "Max Pair Cost ($)", "The most the chase may pay to complete a pair",
+  "$", (0.50, 1.00), ("backtest", "cockpit"))     # no per-surface override

# new shared helper — the single chase ceiling (T1), in strategy/book_math.py
def chase_cap(max_pair_cost: float, entry_price: Any) -> Optional[float]:
    """The highest bid the chase may post for the unfilled leg."""
```

- `GET /api/backtest`: the `pair_cost` query key keeps its spelling (it is already
  short for the knob, and the echoed payload key is `pair_cost` too); its default
  moves `1.05 → 0.99` and it clamps to `[0.50, 1.00]`. `0.0` is no longer accepted as
  "off" — it clamps up to `0.50`.
- `scripts/backtest.py`: `--pair-cost` default moves to `0.99`.
- `scripts/replay_shadow_check.py`: `PAIR_CAPS` becomes `(0.98, 1.00)`; `verdict_leg`
  becomes `gates_pc1.00`.
- `BacktestParams.params_hash()` changes for every config (accepted, `SPEC.md`).

## Tasks

All tasks complete. T8 was folded into T2/T5/T6 rather than run last: each of
those tasks owns a distinct test file, so fixing its tests in the same commit
kept every commit's suite green instead of leaving eight red ones behind.

### [x] T1 — `[Backend/Logic]` One ceiling formula, in one place
**Files**: `strategy/book_math.py` (new `chase_cap`), `tests/test_book_math.py`.
**Do**: add `chase_cap(max_pair_cost, entry_price)` returning
`round(floor((cap - entry + 1e-9) * 100) / 100, 2)`, `None` for an unparseable entry.
Unit-test the flooring, the negative result when `entry > cap`, and the `None` path.
Nothing calls it yet.
**Skill**: `test-driven-development`.
**Verify**: `python -m pytest tests/test_book_math.py -q`.

### [x] T2 — `[Backend/Logic]` Backtest: rename, re-default, revalidate, and delete the entry block
**Files**: `backtest/engine.py` — field `:118`, chase comment `:184`, registry entry
`:211-217`, `bounds_for` docstring `:350-360`, `__post_init__` `:372+`, the
`pair_cost_ok` computation `:1019-1026`, the branch condition `:1079`, the two chase
call sites `:1044-1075`.
**Do**: rename the field and set it to `0.99`; add the `[0.50, 1.00]` `ValueError`
check to `__post_init__`; collapse the registry entry to one range with no override
and reword `why` to say it caps the chase; delete `pair_cost_ok` entirely and reduce
`if not queue_ok or not pair_cost_ok:` to `if not queue_ok:`; route both chase sites
through `book_math.chase_cap`. Rewrite the `bounds_for` docstring, whose whole worked
example was this divergence — pick a surviving example or state that none remains.
**Do not**: touch the chase's `min(ask, cap)` / `> resting` logic, or the entry
anchor. The ceiling's behaviour is unchanged; only its name, default, range and the
location of its arithmetic move.
**Skill**: `source-driven-development`, `test-driven-development`.
**Verify**: `python -m pytest tests/test_backtest_engine.py tests/test_docstrings.py -q`.

### [x] T3 — `[Backend/Logic]` Live: same default, same one formula
**Files**: `strategy/live_trader.py` — defaults `:666`, `:959`; the four flooring
copies `:4496`, `:4513`, `:5086`, `:5127`.
**Do**: default `0.98 → 0.99` in both places; replace all four inline floorings with
`book_math.chase_cap`. The `max(0.50, min(1.00, ...))` clamp in `update_config`
`:2998` already matches the new range and stays.
**Do not**: change the preset — `PATIENT_BAND_MAKER` pins `max_pair_cost: 0.98`
explicitly and keeps it; the default is what an unconfigured engine starts at.
**Verify**: `python -m pytest tests/test_live_trader.py tests/test_patient_band_preset.py -q`.

### [x] T4 — `[Test/Parity]` The parity test the issue asks for
**Files**: new `tests/test_chase_cap_parity.py`, reusing `_snap` / `_drive_live` from
`tests/test_entry_anchor_parity.py`.
**Do**: one shared snapshot sequence per scenario, both engines, assert the chased
quote lands on the identical price: (a) the ask is below the ceiling → both chase to
the ask; (b) the ask is above the ceiling → both stop at `floor((cap - entry)*100)/100`
and never above it; (c) `entry + chased <= max_pair_cost` holds in both after every
tick; (d) `entry` above the cap → neither engine raises the quote.
**Verify**: `python -m pytest tests/test_chase_cap_parity.py -q`.

### [x] T5 — `[API/Dashboard]` Drop the off switch, unify the range
**Files**: `server/osc_dash.py` (`_one` docstring `:556-561`, query default `:624`,
clamp `:701`, echo `:771`, `:989`, the Backtest control and its toggle `:2409-2420`,
the Cockpit control `:2792-2793`, `togglePairCostInput` `:3934+`, the request builder
`:4034-4035`, the reset block `:4313-4317`, the preset block `:4346-4355`, the
`required` id lists `:4346`, `:6789`), `tests/test_osc_dash_integration.py`.
**Do**: delete the toggle, its label span, its JS function and every call to it;
the number input becomes a plain enabled field with `min="0.5" max="1"` and
`value="0.99"`; rename `data-param` to `max_pair_cost` on both controls; update the
two reset/preset defaults; reword the `_one` docstring, which cites this exact
divergence as its reason for existing.
**Skill**: `frontend-ui-engineering` (the panel must not be left with a hole),
`api-and-interface-design`.
**Verify**: `python -m pytest tests/test_osc_dash_integration.py tests/test_param_registry.py -q`,
then a browser check of the Backtest tab (no toggle, field enabled at 0.99, a run
still returns fills) and the Cockpit tab.

### [x] T6 — `[Backend/CLI]` Scripts
**Files**: `scripts/backtest.py` `:94`, `:103`; `scripts/sweep_backtest.py` `:167`,
`:184`, `:204`, `:221`, `:232-238`, `:324`, `:385`; `scripts/replay_shadow_check.py`
`:53-73`, `:113-120`, `:309`.
**Do**: rename every pass-through. `sweep_backtest.py`'s pair-cost grid is
`[1.01, 1.02, 1.03, 1.05, 1.10]` — every value is now out of range and would raise;
replace it with `[0.96, 0.97, 0.98, 0.99, 1.00]`, which is the same five-point sweep
inside the legal range. `replay_shadow_check.py`'s `PAIR_CAPS` `1.05` becomes `1.00`
and the comment explaining 1.05 as "engine default + research §5" is rewritten.
**Verify**: `python -m pytest tests/test_backtest_cli.py tests/test_sweep_backtest.py tests/test_replay_shadow_check.py -q`.

### [x] T7 — `[Research/Logic]` The research simulators
**Files**: `research/sweeps/ev_lab.py` `:486-492`, `:870`, `:1101`;
`research/sweeps/sim2.py` `:177-183`.
**Do**: delete the `(up_ask + dn_ask) <= cap` entry test in both and reduce the branch
to `if not queue_ok:`; rename the field; move the base config `1.05 → 0.99` and the
comparison config `1.01 → 0.99`. `tests/test_ev_sweep_lab.py` scans `sim2.py`'s source
for `p.<field>` exhaustiveness against the dataclass — check whether removing the last
`p.max_pair_cost` read from `sim2.py` trips that scan, and if so record the field as
chase-only rather than weakening the scan.
**Verify**: `python -m pytest tests/test_ev_sweep_lab.py tests/test_selection_bias.py -q`.

### [x] T8 — `[Test]` The tests that encode the deleted gate
**Files**: `tests/test_backtest_engine.py` (`:346`, `:354`, `:361`, `:369` and the
`_params` defaults at `:47`, `:242`, `:980`, `:1218`, `:1237`, `:1331`, `:1344`,
`:1445`), `tests/test_param_registry.py` (`:76`, `:140-150`, `:174`),
`tests/test_backtest_cli.py` (`:79`, `:129`),
`tests/test_replay_shadow_check.py` (`:124`),
`tests/test_osc_dash_integration.py` (`:2449`, `:2475`, `:2609`).
**Do**: the four entry-gate tests assert behaviour this issue deletes — remove them
and name the removal in the commit body (`CONSTRAINTS.md` permits exactly this and
nothing wider). Everything else is a rename or a default change. Retarget
`test_the_registry_does_not_claim_post_init_enforces_every_bound` to `queue_gate`,
which is still registry-bounded and still unvalidated, and add `max_pair_cost` to
`test_registered_bounds_match_post_init_validation`'s parametrize list, where it now
belongs.
**Verify**: the full targeted set from `CONSTRAINTS.md`.

### [x] T9 — `[Docs]` The surfaces that still describe a gate
**Files**: `AGENTS.md` (the `/api/backtest` query list),
`docs/backtest-optimization-results.md:89` (a header note that `pair_cost_gate = 1.05`
described a rule that no longer exists — the numbers themselves stay),
`docs/engine-decision-rules.md:283` and the `backtest/engine.py:352-360` line reference
inside rule 4 (that code is deleted by T2),
`docs/ev-research-findings-2026-09-11.md:208`, `docs/operations.md` if it names the knob.
**Do**: nothing new is decided here. `docs/engine-decision-rules.md` §4 and ADR-0003
are the definition and were agreed before this plan; these files only stop pointing at
a gate that no longer exists.

## Order and commits

T1 → T2 → T3 → T4 (parity proves T2-T3) → T5 → T6 → T7 → T8 → T9.
One atomic commit per task, conventional, scoped (`feat(strategy):`, `fix(backtest):`,
`fix(strategy):`, `fix(dash):`, `chore(research):`, `test:`, `docs:`). Branch
`fix/max-pair-cost-chase-only-227` off `master`.

T8 is last among the code tasks on purpose: the earlier tasks will each break a
handful of the tests it owns, and fixing them once, deliberately, beats patching the
same file eight times.

## Improvement proposed (operator decides — not folded in silently)

**Make the ceiling structural, not just renamed.** The issue asks for one name, one
default and one range. It does not ask for one *formula* — and the formula is copied
six times today: twice in `backtest/engine.py` and four times in `strategy/live_trader.py`,
each with its own `+ 1e-9` and its own `round(..., 2)`. That is the same shape as the
fill rule before #226, where four copies of one predicate is exactly how they drifted
apart. A `chase_cap()` in `strategy/book_math.py` (T1) makes the two engines unable to
disagree about the ceiling instead of merely asserted to agree, and shrinks T2 and T3
to call sites. Cost: one ten-line function, in the module both engines already import.
**Adopt / defer / drop?** The plan above assumes *adopt*; dropping it means T1
disappears, T2 and T3 edit six inline copies in place, and T4's parity test carries
the whole guarantee.
