# Plan — Issue #351: bug(collector): dashboard-started collector never connects the CLOB socket

Branch: i351/dashboard-collector-ws-never-connects | Issue: #351

## Classification
- Size tier: **Standard** (2–5 files, one module-internal decision, but the root cause is
  unconfirmed — the issue's diagnosis is a well-argued hypothesis, not a proven fact).
- Task type: **Debug** (primary) + Code. Downstream stations pick Debug reviewers/tests.
- Stack: Python, pytest (targeted suites only per AGENTS.md; CI is the merge gate).

## CodeRabbit plan intake
- No `coderabbitai` plan comment on the issue (verified: the only comment is the owner's
  measurement report). Cost note recorded here so Stations III–V never re-check.

## Open questions resolved from code
- The issue's spin hypothesis (`streaming.py` empty `token_ids` → infinite `continue`) is
  `[UNVERIFIED]` — it explains "not trying" (`ws_reconnects=0`, `consecutive_failures=0`),
  but a subprocess started via `api_collector_start` with the same interpreter/cwd should
  behave identically to a terminal run. Plan treats the root cause as open and instruments
  first (reproduce → observe → fix), per `debugging-and-error-recovery`.
- `api_collector_start` (`server/osc_dash.py:3396-3421`) spawns
  `subprocess.Popen([sys.executable, "-m", "scripts.collect_ticks"], cwd=ROOT,
  stdout=DEVNULL, stderr=DEVNULL)`. Same cwd and interpreter as a terminal run — so the
  terminal-vs-dashboard difference is NOT cwd/cwd, and stderr swallowing can only hide
  evidence, not cause the difference. The remaining env deltas (event-loop policy under an
  already-running asyncio/uvicorn parent, thread-model of the bridge, console handles on
  Windows) must be observed, not assumed.
- `restart_ws_bridge_if_dead` (`collect_ticks.py:1106`) can only bump `ws_restarts` if
  `is_running` is False — the bridge thread reports alive while spinning on the empty
  token set, consistent with the issue's "restarts stayed 0".
- `update_subscribed_tokens` is called at the END of `poll_once` (collect_ticks.py:1012);
  if the first polls raise before that line, the bridge is never seeded. The dashboard
  path and the terminal path reach the same code, so this alone does not explain the
  difference — but a first-poll exception is swallowed by DEVNULL, so the fix must capture
  child output to make any such failure visible.

## Interface contract (locked before build)
- `start_ws_bridge()` keeps signature/return (`Optional[CLOBStreamCollectorBridge]`,
  `None` = WS disabled) — callers and tests unchanged.
- New: a wall-clock seed guard on the bridge: if no tokens have been handed to the client
  within `WS_TOKEN_SEED_TIMEOUT_S = 30s` of process start, log a `log.warning` every
  30s naming the empty state (never fatal — the REST fallback stays primary; this makes
  the silent spin loud in logs, which the dashboard child previously swallowed into
  DEVNULL).
- New: `--once` regression hook — `main(--once)` prints `ws_bridge_tokens=N` so a smoke
  run can assert the handoff happened; N>0 proves the empty-token window closed.
- `api_collector_start` keeps its response shape; the child's stdout/stderr move from
  DEVNULL to `run/ticks/collector_child.log` (append, per-launch). No API change.
- Manifest keys: no new keys required by the issue; existing `ws_connected`,
  `ws_reconnects`, `book_shadow` unchanged.

## CONSTRAINTS.md (quality guardrails)
- Zero regressions: targeted suites `tests/test_clob_ws_collector.py`,
  `tests/test_collect_ticks_smoke.py`, plus any dashboard collector test file, must pass.
- No new external dependencies.
- Anti-cheat: no skipped tests, no deleted assertions, no disabled linters.
- WS degradation stays non-fatal (REST fallback untouched).
- Performance: the seed-guard check is a timer comparison on a background thread —
  no polling-loop latency impact.

## Improvement proposal (Step 5, evidence-based)
- Evidence (verbatim, issue body): "`start_ws_bridge()` (`scripts/collect_ticks.py:1107`)
  constructs the bridge with **no tokens**: `bridge = CLOBStreamCollectorBridge()` —
  Tokens only arrive later, via `update_subscribed_tokens()` at `collect_ticks.py:1012`,
  which `poll_once` calls **after** the fetch." And: "Capture the child's stdout/stderr
  to a file to see what it actually reports."
- Proposal (hardening, adopt-by-default): seed the bridge with the *known* static token
  set at construction time is NOT possible (tokens are per-window and dynamic), so the
  adopted hardening is the 30s empty-token loud-warning + child-log capture above — both
  directly demanded by the issue's acceptance criteria ("make the failure loud") and
  investigation notes ("capture stderr to a file"). No scope expansion proposed; the
  issue's AC already covers the dashboard-visible degradation signal via #349, which
  stays out of scope here except for the child-log fix that #351 itself requires.

## Tasks (dependency graph, risk-first, atomic)

### T1 [x] [Debug] Reproduce & instrument: prove the empty-token spin under the dashboard path — S
- Files: none (investigation) + optionally a temporary log line, reverted.
- Build a minimal reproduction: spawn the collector exactly as `api_collector_start`
  does (Popen, same DEVNULL→file change from T2 applies here), capture child output,
  observe `token_ids`, `ws_connected`, `ws_reconnects` and the spin branch.
- Verification: captured child log showing the observed failure signature (or its
  absence — either outcome is a recorded finding that pins the root cause).
- Depends on: —

### T2 [x] [Backend/Logic] Capture the collector child's output instead of DEVNULL — S
- Files: `server/osc_dash.py` (`api_collector_start`).
- Open (append) `run/ticks/collector_child.log` for the child's stdout+stderr so the
  `log.info("CLOB market WS connected")` line (or its absence, or an exception trace)
  becomes evidence instead of a black hole. Keep response shape identical.
- Verification: targeted test — spawn path writes to the log file; existing
  collector-start tests stay green.
- Depends on: T1 (the observed signature tells us what the log must reveal).

### T3 [x] [Backend/Logic] Loud empty-token guard in the bridge loop — M
- Files: `strategy/streaming.py` (the `if not self.token_ids:` branch in `run_direct`),
  `scripts/collect_ticks.py` (seed timeout constant + optional surfacing via `get_status`).
- Log a warning (every 30s, with elapsed time and token count) while the client spins on
  an empty token set, so the failure mode is named in logs the moment it happens. If T1
  shows the spin is NOT the root cause, this task's fix adapts to the observed cause
  (e.g. event-loop policy) — the deliverable is "root cause fixed + failure made loud",
  not the hypothesis verbatim.
- Verification: targeted test in `tests/test_clob_ws_collector.py` — a client started
  with no tokens logs the warning and still connects once tokens arrive
  (`update_subscribed_tokens` → `is_connected=True` within the normal window).
- Depends on: T1.

### T4 [x] [Backend/Logic] Regression test for the dashboard-launched path — M
- Files: `tests/test_clob_ws_collector.py` (or a focused new test file if cleaner).
- Test the fix end-to-end at the seam the issue names: bridge constructed empty
  (exactly as `start_ws_bridge()` does), tokens arrive via
  `update_subscribed_tokens(...)` after the loop started, connection proceeds; plus an
  assertion that the seed-guard warning fired during the empty window (capsys/caplog).
- Verification: targeted pytest, <2s, no network (fake transport / `_connect_factory`).
- Depends on: T3.

### T5 [x] [Docs] Runbook note: the child log is the first place to look — XS
- Files: `docs/operations.md`.
- One short paragraph: dashboard-launched collectors write `run/ticks/collector_child.log`;
  `ws_reconnects=0` + `ws_connected=False` = the client is not trying, read the log.
- Verification: review only.
- Depends on: T2.

## Checkpoints
- After T2: child output is being captured (progress note, not a pause — Mode A).
- After T4: regression test proves the empty→seeded→connected path; build report follows.

## Out of scope
- #349 (surfacing `book_shadow`/socket state in the dashboard UI) — separate issue.
- Any change to the #174 Phase 1 comparison logic or Phase 2 switch (NO-GO stands).
