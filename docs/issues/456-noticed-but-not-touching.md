# #456 — Noticed but not touching

| ID | Candidate | Discovering station | Evidence | Status | Disposition |
|----|-----------|--------------------|----------|--------|-------------|
| N1 | Advance pre-quote prices T+1 at fixed `0.50 - offset` with no leg-range check — out of range only for absurd offsets (> 0.40); realistic presets (0.02–0.20) stay inside | iv-review-build-and-pr | `strategy/live_trader.py:4027-4028` | dismissed | Operator-confirmed intended behavior: pre-quoting exists so orders are ready before the window countdown starts; no book exists yet so the fixed 0.50 anchor is by design, and it lands in range for every realistic offset. |
