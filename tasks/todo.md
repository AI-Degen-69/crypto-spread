# TODO — issue #191: settle naked legs the book cannot mark

Branch: `fix/settle-unmarked-naked-191` · Baseline: 856 passed
Spec: `SPEC.md` · Gates: `CONSTRAINTS.md` · Detail: `tasks/plan.md`

- [x] **T0** Comment on issue #191 correcting the Impact claim: the sweep
      headline is already settlement-corrected by `ev_lab.summarize`; the bug
      is confined to `backtest/engine.py`.
- [x] **T1** `[Debug]` Two failing tests in `tests/test_backtest_engine.py`:
      unmarked losing leg books `-43.00c` via the latched bid, unmarked winning
      leg books the full win. Must be RED first.
- [x] **T2** `[Backend/Logic]` Pin the untouched paths: marked leg, pair
      capture, stop-out, and the `unresolved` abstention. Green before and after.
- [x] **T3** `[Backend/Logic]` Port `_resolve_exit_bid` stages 1-4 into
      `resolve_naked_settlement`, then redeem, then abstain. Add
      `settled_unmarked` and `settle_source` to `WindowResult` and the export.
- [x] **T4** `[Backend/Logic]` One test per ladder stage plus live's two
      validity edges (bid exactly `0.0`, opposite ask outside `(0.0, 1.0]`).
- [x] **T5** `[Research/Audit]` Add the corrected-bias line to
      `audit_settlement.py` alongside the raw one; note the engine-only scope in
      `RESULTS-ARE-STALE.md`.
- [x] Full suite green (`python -m pytest -q`), then hand off to
      `/iv-review-build-and-pr`.

**Operator decision:** adopted. `resolve_redemption` lives in
`backtest/engine.py`; `ev_lab` and `sim2` import it and their P&L is unchanged.

**Result:** 870 passed (baseline 856 + 14 new). `audit_settlement.py` bias went
from -395.56$ to +7.17$ at size 5.
