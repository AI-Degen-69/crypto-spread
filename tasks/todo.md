# TODO — Issue #230: Rule: one stop threshold — delete `exit_thresh_naked`

- [x] **T0**: Branch `feat/one-stop-threshold-230` off `master` & lock specs
- [x] **T1**: Backtest engine: delete `exit_thresh_naked` and unify exit logic on `exit_thr`
- [x] **T2**: Live engine: delete `exit_thresh_naked` and unify stop loss on `self.exit_thresh`
- [x] **T3**: Stop Loss dedicated parity test suite (`tests/test_stop_loss_parity.py`)
- [x] **T4**: Dashboard API, schemas, and UI cleanup (`server/osc_dash.py`)
- [x] **T5**: Retarget existing test suites, sweeps, and scripts
- [ ] **T6**: Full targeted gates verification & Station IV handoff
