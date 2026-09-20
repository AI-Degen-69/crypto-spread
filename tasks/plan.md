# Plan — Issue #283: Host the tick collector on a managed platform

Stack: Python · test runner: pytest · Size: **Standard** (infra decision +
packaging + runbook docs, one hosting decision) · Type: Research + Docs (+ small Code)

| ID | Tag | Target files | What is built | Helper skill | Verification |
|---|---|---|---|---|---|
| TASK-1 | [Research] | `SPEC.md` §decision | Host pick (Railway/Render/Fly): disk ≥10GB, always-on, restart policy, cost — decision table with evidence | idea-refine | Decision row cites host docs for disk + restart |
| TASK-2 | [Backend/Logic] | `scripts/collector_watchdog.py` (`collector_pids`, `kill`, `start_collector`) | POSIX path for probe/kill/spawn; Windows path byte-identical behavior (unknown ⇒ no action) | test-driven-development | Targeted pytest of touched code passes on Windows |
| TASK-3 | [Backend/Logic] | host config (start cmd, mount, restart policy) + `requirements.txt` | Deploy recipe: install, mount output dir, start watchdog, restart policy | incremental-implementation | `python -m scripts.collect_ticks --once` passes ON the host |
| TASK-4 | [Research] | host logs + `run/ticks/manifest.json` | 1+ hour proof capture: sane `sampling_interval_s`, no wedged events | — | Manifest excerpt + watchdog log excerpt in runbook |
| TASK-5 | [Backend/Logic] | downloaded day file(s) | Pull day files, verify `--gzip` if used, record `sha256` per file | test-driven-development | `verify_tick_data <downloaded-file>` clean |
| TASK-6 | [Docs] | `docs/` runbook (new file) | Deploy / logs / restart / pull-commands, copy-pasteable | documentation-and-adrs | Commands reviewed by running them once |
| TASK-7 | [Debug] | — | Regression gate | — | `pytest tests/test_collect_ticks_smoke.py -q` green; full suite left to CI |

Improvement (adopted by default): day-file pull records `sha256` — charter §3.1
requires one checksum per golden day, so capturing it at download time saves a
re-hash round-trip later.
