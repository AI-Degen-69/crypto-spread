# CONSTRAINTS.md — Issue #313 (dashboard canonical port 5515)

## Hard boundaries
- **Single definition:** `server/ports.py` is the only tracked place defining
  `5515` (`DASHBOARD_HOST`, `DASHBOARD_PORT`, `DASHBOARD_URL`). No other tracked
  file may carry an `8802` port literal (`server/`, `scripts/`, `tests/`,
  `AGENTS.md`, `README.md`, `docs/operations.md`).
- **Isolated runner untouched:** `scripts/crypto-spread-isolated.ps1` keeps
  `$Port = 8888`; the origin allow-list keeps accepting `8888`.
- **Frozen + gitignored untouched:** `docs/issues/*.html` byte-identical;
  never write `run/dash.pids.json`, `logs/*`, `.gstack/`, `.dev-port`.

## Quality guardrails
- Zero regressions: `tests/test_crypto_spread_menu.py` and
  `tests/test_osc_dash_integration.py` (origin-port case `:1615`) must stay
  green; new regression test covers canonical value + no-literal scan.
- Word-boundary port matching in the regression test (naive `8802` substring
  false-matches research floats like `0.008802...`).
- Anti-cheat: no skipping/disabling tests, no assertion deletion, no lint
  suppression, no test weakening.
- No new external dependencies (stdlib only + existing `pwsh` probe); no new
  env-var/CLI port override surface.

## Out of scope (do not touch)
- `docs/issues/*.html`, `docs/glossary.md`, `i312/golden-dataset-research-cut`,
  runtime artifacts, port override mechanisms.
