# Noticed-but-not-touching — Issue #419 (Station III)

| ID | Candidate | Discovering station | Evidence | Status | Resolution |
|---|---|---|---|---|---|
| N1 | `renderCockpitUI()` never hydrates `cockpitPairCost` from engine state (offset/exit/reversal/quote are hydrated; pair cost keeps whatever the input held) | III | `server/osc_dash.py` hydration branches (running + first-init) | published | #421 |
