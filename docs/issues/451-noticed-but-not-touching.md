# #451 — Noticed but not touching

| ID | Candidate | Discovering station | Evidence | Status | Disposition |
|----|-----------|--------------------|----------|--------|-------------|
| N1 | Malformed `_resolve_exit_bid(slug, mstate, naked_side)` fallback passes 3 args to a `(self, mstate, side)` tuple-returning helper — latent crash when the dead-zone naked bid is `None` | iii-build-plan | `strategy/live_trader.py:4925` vs `strategy/live_trader.py:5056` | open | — |
