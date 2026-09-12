# Todo: Issue #132 — collector→overview bridge + auto-rebuild

- [x] Task 1: Shared module `strategy/windows.py` (classify/finalize/summary/atomic-write)
- [x] Task 2: Offline rebuild routed through shared module (signatures preserved)
- [x] Task 3: Collector accumulates mids/touch_pairs + closes into dataset (best-effort)
- [x] Task 4: Dashboard `POST /api/rebuild` + provenance fields in `/api/oscillation`
- [x] Task 5: Dashboard HTML/JS — Rebuild button + badge + tooltip + live goal bar
- [x] Task 6: Tests — shared module + collector closure + endpoint/provenance
- [x] Task 7: Rebuild accuracy over `.jsonl` + `.jsonl.gz` fixtures
- [x] Task 8: Full regression gate (`pytest -q` + real rebuild run)
