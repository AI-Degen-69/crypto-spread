# TODO — Issue #373

- [x] T1 — add `GAMMA_HOST` / `CLOB_HOST` to `strategy/markets.py` (XS) — no deps
- [x] T2 — new `tests/test_markets_hosts.py` smoke guard (S) — depends T1
- [x] **Checkpoint** — regression guard proven able to fail (4 failed w/o T1)
- [x] T3 — targeted verification sweep, no full suite (XS) — depends T1, T2

**Not here:** #374 rewiring consumers · #375 discovery logic ·
`collect_ticks.py:280` clock divergence.

**Done:** 78 targeted tests passing; recorder imports; full suite deferred to CI.
**Next:** `/iv-review-build-and-pr`