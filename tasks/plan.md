# Plan — Issue #285: Railway trial + Google Drive file store

Stack: Python · test runner: pytest · Size: **Standard** (shipper module +
watchdog hook + platform config + runbook, one deployment decision) · Type: Code + Docs

| ID | Tag | Target files | What is built | Helper skill | Verification |
|---|---|---|---|---|---|
| TASK-1 | [Backend/Logic] | `scripts/ship_to_drive.py` (new) | Closed-day detection (`now_day_key` past file day), sha256 + manifest snapshot, `shipped.json` state, rclone subprocess with env config, retry-with-backoff, never-raises contract | test-driven-development | New pytest: closed-vs-live, sidecar contents, state skips re-upload, failure returns False |
| TASK-2 | [Backend/Logic] | `scripts/collector_watchdog.py` (shipper pass) | Behind `DRIVE_REMOTE`: one shipper pass per loop iteration; default off, probe/kill/spawn untouched | test-driven-development | Watchdog tests green; off-by-default proven by test |
| TASK-3 | [Backend/Logic] | `nixpacks.toml` (new) | Python + rclone apt; start command with `COLLECT_OUT` + `COLLECT_EXTRA_ARGS=--gzip`; 500MB volume mount documented | incremental-implementation | Config reviewed against nixpacks schema; `--once` smoke locally |
| TASK-4 | [Docs] | `docs/collector-hosting-runbook.md` | Railway+Drive path: service setup, one-time Drive auth (operator PC), logs, pull-from-Drive + checksum | documentation-and-adrs | Commands reviewed; auth steps executable by operator |
| TASK-5 | [Debug] | — | Regression gate | — | `pytest tests/test_collector_watchdog.py tests/test_collect_ticks_smoke.py tests/test_ship_to_drive.py -q` green; full suite left to CI |
| TASK-6 | [Research] | operator, on host | Deploy + first Drive delivery (needs trial account + Drive token — operator hands) | — | Day file in Drive with `.sha256`; pulled file verifies clean |

Improvement (adopted by default): each shipped day carries a manifest snapshot
alongside the `.sha256` — #281's provenance audit then needs Drive only, no
server access.
