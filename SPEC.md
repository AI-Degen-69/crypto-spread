# SPEC — Issue #313: dashboard single canonical port constant (5515)

Per-issue specification. Source: issue #313 body, code-verified against the repo.

## Context

The dashboard port `8802` is duplicated as a literal in six tracked consumer
groups (Python server, PowerShell launcher, paper observer, tests, living docs)
with nothing linking them. The operator wants the dashboard on **5515** with
exactly one tracked place defining it.

## Verified Code Facts (checked, not assumed)

- `server/osc_dash.py:9` docstring says "Serves on :8802"; `:207`
  `allowed_ports = {8802, 8888, 8000, 80, 443}` with runtime `server_port` add
  at `:208-209`.
- `server/osc_dash.py:39` imports `strategy.live_trader` (heavy) — therefore
  `scripts/observe_paper.py` must NOT import `server.osc_dash`; a dependency-free
  leaf module is required.
- `scripts/crypto-spread-menu.ps1:28` `$Port = 8802`, `:29` `$DashUrl`,
  already sets `$env:PYTHONPATH` to project root at `:38` — a
  `python -c "from server.ports import DASHBOARD_PORT; print(...)"` probe works
  without new path plumbing.
- `scripts/crypto-spread-isolated.ps1:34` `$Port = 8888` — deliberately separate,
  passes `--port 8888` straight to uvicorn; never goes through the canonical constant.
- `scripts/observe_paper.py:34` `BASE_URL = "http://127.0.0.1:8802"` with
  `--url` override at `:142/150` — the override must keep working.
- `tests/test_crypto_spread_menu.py:25` asserts `"8802" in content`; `:38/:47`
  PID fixture uses port 8802; `:56` asserts `8802` in `status` stdout.
- Naive substring search for `8802` false-matches research numbers, e.g.
  `research/sweeps/phase1_1d.json:6615` `"pair_rate": 0.008802816901408451`.
  The regression test must match port literals (`:8802`, `"8802"`, `= 8802`,
  `port ... 8802`), not any `8802` substring.

## Resolved Open Questions (from code, per issue defaults)

1. Where does the single definition live / how does PS read it? → (a):
   new leaf `server/ports.py` with `DASHBOARD_HOST`, `DASHBOARD_PORT = 5515`,
   `DASHBOARD_URL`; Python imports it; the `.ps1` launcher resolves `$Port`
   once via the python probe with loud failure. Parity test kept as backstop.
2. Runtime override? → No. Plain constant `5515`; isolated runner keeps its own
   `8888` for bind + allow-list.
3. Allow-list leftovers? → Replace `8802` with the constant; keep
   `8888`/`8000`/`80`/`443` + runtime add as-is.

## Acceptance Criteria (from the issue)

- [ ] One tracked location defines the port once (`5515`); repo search over
  `server/`, `scripts/`, `tests/`, `AGENTS.md`, `README.md`,
  `docs/operations.md` finds no `8802` port literal.
- [ ] Every tracked consumer resolves from it: `server/osc_dash.py`
  (docstring + allow-list), `scripts/crypto-spread-menu.ps1` (probes, PID
  registry, uvicorn args, UI text), `scripts/observe_paper.py` (`BASE_URL`).
- [ ] Isolated runner still binds `8888`; `_verify_safe_origin` accepts both
  canonical port and `8888`.
- [ ] Regression test asserts canonical value `5515` + no hardcoded port
  literal can silently return.

## Explicit Out of Scope

- Frozen pages `docs/issues/*.html` (12 files) — byte-identical.
- Gitignored runtime: `run/dash.pids.json`, `logs/*`, `.gstack/` logs, `.dev-port`.
- No env-var/CLI port override. No `docs/glossary.md` change.
- Nothing on `i312/golden-dataset-research-cut`.
