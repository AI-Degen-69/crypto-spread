# Noticed-but-not-touching — Issue #438 (Station III)

Candidates spotted while building `tasks/plan.md` for #438. Only Station VI
(`vi-close-pipeline`) resolves rows here; other stations append `open` rows.

| ID | Candidate | Discovering station | Evidence | Status | Resolution |
|---|---|---|---|---|---|
| N1 | `BOOK_SHADOW_TOLERANCE` is defined **twice** — once in the collector and once in the dashboard — and it is the same literal that decides whether a comparison counts as `divergent`. Two independent definitions of one threshold can drift apart silently, so the badge an operator reads and the metric it summarises could end up disagreeing. Out of #438's scope by construction: `CONSTRAINTS.md` §4 forbids touching the dashboard copy, because this issue reads the metric rather than redefining it. | III | `scripts/collect_ticks.py:553` (`BOOK_SHADOW_TOLERANCE = 0.001`) vs `server/osc_dash.py:4524` (the same literal, defined independently) | open | — |
