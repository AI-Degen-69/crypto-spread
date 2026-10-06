# #451 — Noticed but not touching

| ID | Candidate | Discovering station | Evidence | Status | Disposition |
|----|-----------|--------------------|----------|--------|-------------|
| N1 | Malformed `_resolve_exit_bid(slug, mstate, naked_side)` fallback passes 3 args to a `(self, mstate, side)` tuple-returning helper — `TypeError` on every tick when the dead-zone naked bid is `None`, so the configured exit never fires and the remaining markets lose their strategy update for that tick | iii-build-plan | `strategy/live_trader.py:4934` vs `strategy/live_trader.py:5087` (re-verified at the #455 closeout) | published | Published as #459 after re-verifying the defect against current master: the call site still passes 3 positional args to a 2-arg helper that returns a tuple, and `_run_loop` only logs the resulting `TypeError` once per second. |
