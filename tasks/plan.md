Branch: i445/featbacktest-ship-overnight-btceth-winners-as-load | Issue: #445

# Implementation Plan — Overnight BTC/ETH Winners as Loadable Backtest Preset

## Size & Stack
- Tier: **Standard** — 3 files touched (seed JSON, server hook, tests), one architectural decision (static seed + copy-if-missing vs generated record).
- Task type: **Code** (Backend/Logic). Stack: Python, FastAPI (`server/osc_dash.py`), pytest. No UI change (existing #413 template card).

## CodeRabbit Intake Note
- Plan prompt posted on #445; no bot reply arrived before planning. Nothing adopted, nothing rejected, nothing `[UNVERIFIED]` from CodeRabbit — plan built from repo research instead.

## Resolved Open Questions (needs-answers removed 2026-10-05)
- **Seeding path:** resolved from code. Load rebuilds params from `request_args` and 409s on hash drift (`server/osc_dash.py:4318-4323`); `series` accepts `"BTC,ETH"` (`backtest/selection.py:22`, tokens case-insensitive); no mkdir/startup copy exists today. Decision: static seed JSON under `backtest/seed_templates/` + copy-if-missing hook at server startup computing `params_hash` fresh via `_prepare_backtest_request`.
- **type-design-analyzer:** skipped — record schema frozen by `REQUIRED_RECORD_KEYS` (`backtest/templates.py:22-33`); no new invariants to design (not invented, recorded here).
- **code-explorer:** skipped — execution path (`save → list → load → apply`) already traced via tests + endpoints during research.

## Tasks

### [ ] Task 1: [Backend/Logic] Seed JSON + startup copy-if-missing hook (M)
- **Files:** `backtest/seed_templates/overnight-majors-btc-eth.json` (new), `server/osc_dash.py` (hook near `BACKTEST_TEMPLATES_DIR`)
- **Depends on:** none (riskiest: hash/registry coupling — first)
- **Description:** Commit the seed record (seven winning knobs, `series: "BTC,ETH"`); on startup copy it into `run/backtest_templates/` only when missing, recomputing `params_hash` via `_prepare_backtest_request` (adopted improvement, see below). Never overwrite existing files.
- **Verification:** manual startup check + Task 2 tests green.

### [ ] Task 2: [Backend/Logic] Seed + hook tests (S)
- **Files:** `tests/test_backtest_templates.py` (append) or new `tests/test_seed_templates.py`
- **Depends on:** Task 1
- **Description:** Seed validates via `validate_record`; rebuilt hash from `request_args` matches stored hash; hook creates missing file, never overwrites existing; seeded record loads 200 through the API (existing TestClient fixture pattern, `test_backtest_templates.py:230`).
- **Verification:** `python -m pytest tests/test_backtest_templates.py -q` green.

### [ ] Task 3: [Verification/QA] Checkpoint + acceptance sweep (S)
- **Files:** none (verification only)
- **Depends on:** Task 1, Task 2
- **Description:** Confirm the four SPEC.md acceptance criteria end to end (load 200, fresh-checkout seeding, no-overwrite, targeted suite green).
- **Verification:** `python -m pytest tests/test_backtest_templates.py -q`.

**Checkpoint:** after Task 1 — seed file + hook live, suite green (one-line progress note in Mode A).

## Improvement Proposal (adopted)
- **Hash the seed at copy time, not at author time.** Evidence verbatim: `if stored_hash and current_hash != stored_hash:` → `return JSONResponse(status_code=409, ... "template stale ...")` (`server/osc_dash.py:4318-4323`). A hardcoded hash rots on the next registry change; recomputing via `_prepare_backtest_request` during the startup copy makes drift impossible. Folded into Task 1 (hardening, not scope expansion).
