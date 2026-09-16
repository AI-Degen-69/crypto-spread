# Plan — Issue #228: `quote_range` replaces `entry_band` and `adverse_open`

**Size**: Large — both engines (gate blocks + re-entry mechanism + preset),
a public API contract (`/api/backtest` query keys), two dashboard controls, a CLI
flag, two research simulators, three scripts, ~14 test files, one test file
deleted with the preset. Two behaviour deletions plus one re-entry deletion.
**Type**: Code (+ a Docs slice).
**Stack**: Python 3.12, FastAPI, pytest. Targeted tests only locally; CI is the
merge gate (`AGENTS.md`).
**Spec**: `SPEC.md`. **Gates**: `CONSTRAINTS.md`. **Rule of record**:
`docs/engine-decision-rules.md` §6.
**Interview**: one question asked — the `patient_band_maker` preset is
**deleted** (operator decision 2026-09-16), not redefined. Everything else was
settled by the issue + rule text, so `interview-me` asked nothing further.

## Contract changes

```python
# backtest/engine.py
- entry_band: float = 0.0
+ quote_range: tuple[float, float] = (0.10, 0.90)   # validated: 0.0 <= lo < hi <= 1.0

# strategy/live_trader.py
- self.entry_band: float = 0.0                      # + update_config + preset-table read
+ self.quote_range: tuple[float, float] = (0.10, 0.90)

# per-tick placement hold, both engines (two-sided mid only, never latched):
quotable &= (range_mid is None or (QUOTE_LO <= range_mid <= QUOTE_HI))
```

- `GET /api/backtest`: `entry_band` query key becomes `quote_lo` / `quote_hi`
  (defaults 0.10 / 0.90, clamped to `[0.0, 1.0]`, lo < hi enforced).
- `scripts/backtest.py`: `--entry-band` becomes `--quote-lo` / `--quote-hi`.
- `research/sweeps/sim2.py`: `entry_band` argument becomes `quote_range`
  (per-tick, never-latched — not the old first-tick latch under a new name).
- Registry: `entry_band` entry deleted; `quote_range` registered as a
  **structural limit** (no per-surface override), per ADR-0003.

## Tasks

### [x] T0 — Branch + spec lock (done in Station II)
Branch `fix/quote-range-228` off `master`. `SPEC.md`, `CONSTRAINTS.md`,
`tasks/plan.md`, `tasks/todo.md` written. No code touched.

### [ ] T1 — `[Backend/Logic]` Backtest: delete both gates, add the range
**Files**: `backtest/engine.py` — field `:170`, registry entry `:229`,
`__post_init__` `:439-442`, locals `:793`, flags `:804-811`, adverse block
`:932-946`, band block `:957-963`, re-entry block `:965-1018`, fill guards
`:1149-1150` (keep timeout/late-start meaning), timeout re-check `:1261-1264`.
**Do**: delete the field + validation + registry entry; add `quote_range` with
`(0.10, 0.90)` default and `0.0 <= lo < hi <= 1.0` `ValueError` check; delete
the adverse-gate, band-gate and drift-skip re-entry blocks with their flags
(`band_gate_evaluated`, `adverse_gate_evaluated`, `adverse_skipped`,
`reentry_count`); delete `reentry_*` knobs with no remaining readers (verify
zero readers at build time); add the per-tick range hold on the two-sided mid
next to `no_book_hold`, gating placement only. The range check reuses the
anchor's two-sided mid (`anchor_mid`, `:876`) — no second `_two_sided_mid`
call (operator-approved 2026-09-16, דרך ב'). Timeout/late-start
`entry_cancelled` sets stay untouched.
**Skill**: `test-driven-development`.
**Verify**: `python -m pytest tests/test_backtest_engine.py -q`.

### [ ] T2 — `[Backend/Logic]` Live: delete both gates + re-entry, add the range
**Files**: `strategy/live_trader.py` — preset table `:660-669` (preset deleted,
T5), state fields `:734-748`, reset block `:3666-3704`, re-entry method
`:4160-4260`, open-gate block `:4655-4662`, band block `:4673-4710`,
cancellation block `:4728-4830` (keep timeout/late-start arms), placement
gating `:4853-4890`, `entry_controls_armed` `:4292`, stats `:80-82,1019,2668,2726`.
**Do**: delete `entry_band` plumbing (`update_config`, preset-table read,
`:3011` clamp, `:2866` drift check, `:4040` mirror check); delete the
open-gate/adverse snapshot, the band block + hold, `_maybe_reenter_drift_skipped`
with its telemetry/state, and `band_skip_stats`; add the per-tick range hold on
the two-sided mid at the placement gate, reusing the anchor mid value already
in scope — no second mid computation (operator-approved 2026-09-16, דרך ב'). `open_mid/open_drift` telemetry fields
stay only if another reader uses them — otherwise they go with the gate.
**Verify**: `python -m pytest tests/test_live_trader.py tests/test_entry_timeout.py -q`.

### [ ] T3 — `[Test/Parity]` Range parity: entry, exit, re-entry to the range
**Files**: new `tests/test_quote_range_parity.py`, reusing `_snap` /
`_drive_live` from `tests/test_entry_anchor_parity.py`.
**Do**: one shared snapshot sequence per scenario, both engines: (a) mid
outside the range at open → no quote on either leg that tick; (b) mid returns
inside → quoted again the same window, at the current mid; (c) boundary mids
0.10 / 0.90 are inside (inclusive); (d) an already-resting quote stands while
the mid is outside (no cancel); (e) narrow custom range (e.g. `(0.40, 0.60)`)
holds placement outside it in both. Assert on quotes/fills, never on markers.
**Skill**: `test-driven-development`.
**Verify**: `python -m pytest tests/test_quote_range_parity.py -q`.

### [ ] T4 — `[API/Dashboard]` Band inputs become range inputs
**Files**: `server/osc_dash.py` (query model `:638`, clamp `:688-691`, spec
pass-through `:712`, echo `:783,:1000`, cockpit schema `:1380`, payload
`:1504`, Backtest control `:2405-2406`, Cockpit control `:2779-2780`, request
builder `:4045`, preset/validation maps `:5733-5738`), `tests/test_osc_dash_integration.py`.
**Do**: replace both band inputs with lo/hi number inputs (`min="0" max="1"`,
`value="0.10"/"0.90"`, plain fields, no toggle — the #227 pattern); rename
`data-param` to `quote_lo`/`quote_hi`; register `quote_range` as structural
(no per-surface override). Browser-check both tabs after.
**Skills**: `frontend-ui-engineering`, `api-and-interface-design`.
**Verify**: `python -m pytest tests/test_osc_dash_integration.py tests/test_param_registry.py -q` + browser check.

### [ ] T5 — `[Backend/CLI]` Scripts, sims, preset deletion
**Files**: `scripts/backtest.py` (`--entry-band` → `--quote-lo/--quote-hi`);
`scripts/replay_shadow_check.py` (gates legs keep `entry_delay`; delete the
`entry_band` leg of the mirror + its recorded comparison);
`scripts/shadow_ev_pilot.py` (band variant — delete or re-scope to delay);
`research/sweeps/sim2.py` (`entry_band` arg → `quote_range`, per-tick);
`research/sweeps/ev_lab.py` (`UNSUPPORTED_KNOBS` follows the rename);
`research/sweeps/selection_bias.py` (band cfg follows);
`strategy/live_trader.py` preset block + `tests/test_patient_band_preset.py`
(deleted with the preset — the one file deletion `CONSTRAINTS.md` permits).
**Verify**: `python -m pytest tests/test_backtest_cli.py tests/test_sweep_backtest.py tests/test_replay_shadow_check.py tests/test_ev_sweep_lab.py -q`.

### [ ] T6 — `[Test]` The tests that encode the deleted gates
**Files**: every test file with `entry_band`/`adverse_open` refs (14 files per
the Station II scan — heaviest: `test_live_trader.py` (48), `test_backtest_engine.py`
(15), `test_osc_dash_integration.py` (16), `test_patient_band_preset.py` (16,
deleted in T5), `test_ev_sweep_lab.py` (10)).
**Do**: tests asserting band/adverse/re-entry behaviour are removed with the
behaviour (removal named in the commit body); timeout/late-start tests stay;
everything else is a rename. Fold into T1/T2/T4/T5 per file (the #227 pattern:
one pass per file keeps every commit green) rather than running last.
**Verify**: the full targeted set from `CONSTRAINTS.md`.

### [ ] T7 — `[Docs]` Surfaces that still describe the gates
**Files**: `docs/engine-decision-rules.md` §5 (`adverse-open` gate — now
historical, mark it), `:523` (flag paragraph), `docs/operations.md` (band
references), `AGENTS.md` (`/api/backtest` query list), research finding docs
that quote band numbers as live config (mark as unreproducible records, the
#227 pattern — numbers stay).
**Do**: nothing new is decided; rule §6 is the definition. Mark, do not
rewrite history.

## Order and commits

T0 → T1 → T2 → T3 (parity proves T1–T2) → T4 → T5 → T6 (folded per-file) → T7.
One atomic commit per task, conventional, scoped. Branch
`fix/quote-range-228` off `master`.

## Improvement decided (operator approved 2026-09-16 — adopted)

**The range check reuses the anchor mid, no second computation.** Both engines
evaluate the range on the anchor's two-sided mid value (`anchor_mid` in
backtest, the anchor mid in scope in live) instead of calling `_two_sided_mid`
a second time — one mid definition, covered by the parity test together with
the anchor. Folded into T1/T2 above.
