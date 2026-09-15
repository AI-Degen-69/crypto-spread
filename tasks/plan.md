# Plan — issue #191: settle naked legs the book cannot mark

**Size: Standard.** 3 code files plus tests and one research doc. **Type:
Debug + Backend/Logic.** Stack: Python 3.12 / FastAPI, `pytest`
(baseline **856 passed**). No UI surface is touched, so no design skills apply.

Branch: `fix/settle-unmarked-naked-191`.
Specification: **`SPEC.md`**. Quality gates: **`CONSTRAINTS.md`**.

## Two things the issue body got wrong, found while planning

Verified every claim against the code first.

**1. The sweep results are not affected.** The issue claims every `ex=none`
number in `research/sweeps/` is inflated, including the +37.38$ headline in
`overnight_report.md`. Not accurate — the research layer already carries this
correction. `ev_lab.fast_simulate` and `sim2.sim2` both detect the empty-bid
case, set `naked_none`, and record `settle_won` / `settle_delta`;
`ev_lab.summarize` applies it by default (`settle_correct=True`) and
`phase5_band.py:100` passes it explicitly. The bug is confined to
`backtest/engine.py` — the production replay behind `scripts/backtest.py`,
which has no `naked_none` concept at all. That is exactly why the faithful CLI
replay showed series with 0.0% pair rate *and* 0.0% exit rate still returning
positive P&L. Corrected in a comment on the issue (T0, done).

**2. The proposed fix was weaker than what already exists.** The issue proposes
redeeming at 1/0 from the final mid. But issue #160 already shipped
`LiveTrader._resolve_exit_bid` (`strategy/live_trader.py:5386-5438`) — a
five-stage resolution ladder that *raises* rather than assume a price. The
backtest stops at stage 1 and books zero on failure. So this is a **parity gap
against the reference implementation**, not a missing feature, and the fix is to
port the ladder rather than invent a second rule.

On the worked example (held DOWN leg resting 0.470, final `DOWN: bid None`,
`UP: ask None`, final mid 0.995):

| Path | Mark | Booked |
|---|---|---|
| engine today | none | **0.00c** |
| live's ladder | stage 3, latched DOWN bid 0.04 | **-43.00c** |
| redemption only | DOWN settles 0.00 | **-47.00c** |

The design in `SPEC.md` uses the ladder first and falls back to redemption only
when all four stages fail — strictly closer to live, and strictly more
conservative than redeeming straight to 0.

## Improvement pass — one shared resolver instead of a third copy

The last-mid/redemption arithmetic already exists **twice**, inline and
near-identically, in `ev_lab.fast_simulate` and `sim2.sim2`. A third copy inside
`_simulate_window` is how issue #182 happened: the audit and the simulator each
grew their own anchor rule and disagreed by ~40% in the script whose job was to
correct a bias.

Proposed: one exported function in `backtest/engine.py` —

```python
def resolve_naked_settlement(
    snaps: list[dict], held_up: bool, resting: float,
) -> tuple[float | None, float, str]:
    """(mark, delta_cents, source) for a naked leg at window close.

    source in {direct_bid, complement_ask, latched_bid,
               latched_complement_ask, redeemed, unresolved}.
    `mark` is None for redeemed/unresolved (no closing trade, so no fee).
    """
```

`_simulate_window` calls it; `ev_lab` and `sim2` import it and keep returning
`settle_won` / `settle_delta` for `summarize` exactly as today — their P&L
unchanged, only the duplicated arithmetic disappears. Three call sites, one
definition.

*Operator decision required before T3 — adopt, defer, or drop.* T1, T2, T4 and
T5 are unaffected either way.

## Tasks

### T0 — `[Docs]` Correct the issue body ✅ done
- Comment posted on #191 correcting the Impact claim.

### T1 — `[Debug]` Pin the bug with two failing tests
- **Files:** `tests/test_backtest_engine.py`
- Build the worked example: DOWN fills at 0.470, UP never fills, DOWN's final
  `best_bid` is `None`, latched DOWN bid 0.04, final mid 0.995. Assert the
  window books `-43.00c`, not `0.00c`. Mirror it for a winning unmarked leg.
- **Skill:** `test-driven-development`. **Verify:** both tests **fail** on the
  current engine. Red is the deliverable.

### T2 — `[Backend/Logic]` Pin the paths that must not move
- **Files:** `tests/test_backtest_engine.py`
- One test per untouched path: held leg with a real final bid keeps its exact
  `pnl_cents` / `fees_cents` / `settlement_mid`; a pair-captured window is
  unchanged; a stopped-out window is unchanged; a window with no usable
  reference mid still books `0.00c` and reports `settle_source == "unresolved"`.
- **Skill:** `test-driven-development`. **Verify:** green on master, still green
  after T3.

### T3 — `[Backend/Logic]` Port the ladder into the engine
- **Files:** `backtest/engine.py` (`resolve_naked_settlement`,
  `_simulate_window` at 1023-1035, `WindowResult`, the `trades_sample` dict at
  ~1136); plus `research/sweeps/ev_lab.py` and `research/sweeps/sim2.py` if the
  shared-resolver improvement is adopted.
- Mirror `_resolve_exit_bid` stages 1-4 against `window_snaps`, then redeem from
  the final mid, then abstain. Add `settled_unmarked: bool` and
  `settle_source: str` to `WindowResult` and the export. No taker fee on a
  redemption.
- **Skill:** `source-driven-development`, `incremental-implementation`.
- **Verify:** `python -m pytest -q tests/test_backtest_engine.py tests/test_sweep_backtest.py`, then the full suite.

### T4 — `[Backend/Logic]` One test per ladder stage
- **Files:** `tests/test_backtest_engine.py`
- Force each stage in turn (`direct_bid`, `complement_ask`, `latched_bid`,
  `latched_complement_ask`, `redeemed`, `unresolved`) and assert both the P&L
  and the reported `settle_source`. Include the two validity edges live
  enforces: a held-leg bid of exactly `0.0` falls through, and an opposite ask
  outside `(0.0, 1.0]` falls through.
- **Skill:** `test-driven-development`. **Verify:** targeted file green.

### T5 — `[Research/Audit]` Close the loop on the evidence
- **Files:** `research/sweeps/audit_settlement.py`,
  `research/sweeps/RESULTS-ARE-STALE.md`
- Add a corrected-bias line to the audit **alongside** the existing raw line —
  the raw number is the evidence in #191 and must survive. Note in
  `RESULTS-ARE-STALE.md` that pre-fix `scripts/backtest.py` output for
  hold-to-settle configs is invalid, and that the sweep JSONs were already
  corrected by `summarize`.
- **Skill:** `documentation-and-adrs`.
- **Verify:** `python research/sweeps/audit_settlement.py` — corrected bias
  approximately 0, raw bias printed and unchanged.

## Estimate

About 3 hours: ~45 min on T1/T2 fixtures, ~45 min on T3 (the ladder is four
stages plus two fallbacks), ~45 min on T4, ~20 min on T5, ~30 min for the full
suite and PR.
