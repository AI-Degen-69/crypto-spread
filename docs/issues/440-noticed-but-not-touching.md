# Noticed-but-not-touching — Issue #440 (Station IV)

Candidates spotted while executing `tasks/plan.md` for #440. Only Station VI
(`vi-close-pipeline`) resolves rows here; other stations append `open` rows.

| ID | Candidate | Discovering station | Evidence | Status | Resolution |
|---|---|---|---|---|---|
| N1 | The collector status tooltip still breaks the shadow metric down per series with GLOBAL per-series rates (`server/osc_dash.py` tooltip rows read `s.divergent / s.comparisons` from manifest `per_series`, which `note_series` accumulates without a gated/blind split). The badge headline is gated since #440, but a series row can show a "rate" that averages both populations — the same defect class §7.3 names, one level down. Out of #440's scope: the issue gates the headline rate; splitting `per_series` needs collector-side gated/blind accumulators plus a manifest shape change. | IV | `server/osc_dash.py:8002` (`s.divergent / s.comparisons`) vs `scripts/replay_socket_reconciliation.py:note_series` (no checkability axis) | open | — |
