# Plan — Issue #200: Menu Collector status shows Could not query even while dashboard collector is running

- **Issue:** https://github.com/AI-Degen-69/crypto-spread/issues/200
- **Branch:** `fix/collector-status-reliability-200` (off `master`)
- **Size tier:** **Small** — 2 application files (`server/osc_dash.py`, `scripts/crypto-spread-menu.ps1`) + tests. Root cause is a per-request O(n) file scan + a 3s menu timeout + a swallowed exception, not a capture-engine bug.
- **Task type:** **Bug fix** (reliability + performance + error reporting).
- **Stack detected:** Python 3.12 / FastAPI (`server/osc_dash.py`), PowerShell 7 (`scripts/crypto-spread-menu.ps1`), pytest (`tests/test_osc_dash_integration.py`).
- **Verification mode:** Automated tests — targeted `python -m pytest tests/test_osc_dash_integration.py -q` per task, then full `python -m pytest -q` before PR. No network/VPN dependency.
- **interview-me:** Skipped — issue already contains exact files/lines, suspected contributors, and acceptance criteria; no ambiguous requirement.
- **Line-number drift note:** Issue cites `server/osc_dash.py:1048-1094` / `:357-366` / `:3654-3712` / `scripts/crypto-spread-menu.ps1:268-279`; current HEAD matches those ranges within the 1048/357/3654 windows — tasks use actual symbols (`api_collector_status`, `_count_lines_fast`, `refreshCollectorStatus`).

## Skills prescribed per task

| Domain | Skill |
| --- | --- |
| Planning | `spec-driven-development`, `constraint-driven-development`, `api-and-interface-design`, `planning-and-task-breakdown` |
| Build & Quality | `test-driven-development`, `debugging-and-error-recovery`, `code-review-and-quality` |
| Performance | `performance-optimization` |
| Git & Ship | `git-workflow-and-versioning` |

---

## Concise spec (embedded — Small tier, no SPEC.md rewrite)

**Goal:** `GET /api/collector/status` and the menu's `Collector:` status line stay trustworthy while the collector is actively writing a growing tick file.

**Root causes (per issue):**
1. `api_collector_status` calls `_count_lines_fast(today_file)` on every request — full streaming read that slows as the file grows (up to 1 GB+), while `api_ticks_manifest` already avoids this with size-based estimation / verify-cache and the code comment at `server/osc_dash.py:369-375` warns against unbounded scans.
2. Menu query uses `TimeoutSec 3` and a `catch` without `$_` (`Csm-Warn "Collector: Could not query /api/collector/status"`), so timeout vs refused vs HTTP error are indistinguishable — unlike the Trading Engine block two lines above which prints `($_)`.
3. Dashboard `refreshCollectorStatus()` has `catch{}` empty, so a slow/failed status is invisible in-browser while the menu surfaces it as a failure.

**Contracts (locked before build):**
- C1 — `GET /api/collector/status` must not run an unbounded per-request scan on a large today's tick file. For files ≥20 MB (same constant as `api_ticks_manifest`), return `int(size / 950)` (bytes 950 heuristic) exactly as that endpoint does; for smaller files, either a one-shot scan or a ≤10s TTL cache is acceptable. `total_ticks_collected` may be an estimate on large files; the field name does not change and tests must accept estimated values.
- C2 — Menu Collector failure message must interpolate the real exception (`$_`), e.g. `Csm-Warn "Collector: Could not query /api/collector/status ($_)` — matching `scripts/crypto-spread-menu.ps1:259`. The static-only string must disappear.
- C3 — Menu query timeout must not be *shorter* than the worst-case status latency on a large file. Either raise the Collector query timeout (e.g. 10s) or keep 3s only when paired with a fast endpoint from C1; the fix must make the pair consistent.
- C4 — `refreshCollectorStatus` empty `catch{}` must become a visible/logged catch (e.g. `catch(e){ console.warn(...) }` or similar) so a flaky endpoint is observable in devtools.
- C5 — Behavior outside scope does not change: collector capture/tape, badge styling, and `/api/live/state` semantics are untouched.

**Out of scope:** `scripts/collect_ticks.py`, tape handling, CLOB, badge styling, `run/` artifacts.

---

## Tasks

### T1 — [Code] Cheap tick count for `GET /api/collector/status`

- **Domain:** `[Code]`
- **Target Files:** `server/osc_dash.py` (`api_collector_status`, nearby `_count_lines_fast` usage)
- **Skill:** `performance-optimization`
- **Description:**
  - Change `api_collector_status` to avoid per-request full scan of today's tick file. For `size >= 20_000_000` return `int(size / 950)` (same heuristic/constant as `api_ticks_manifest`). For smaller files, a direct count or a ≤10s TTL-cached count is acceptable; prefer reusing the existing 950 heuristic pattern.
  - Keep all other fields (`running`, `pid`, `source`, `external`, `manifest_age_sec`, `tape_*`) byte-for-byte, and keep response shape/tests green.
- **Verification:**
  - New/updated test in `tests/test_osc_dash_integration.py` that asserts `api_collector_status` returns successfully and `total_ticks_collected` is consistent with size-based estimate when a large tmp file is present; existing collector-status tests still pass.
  - Manual reasoning: `GET /api/collector/status` no longer calls `_count_lines_fast` path for large files.

### T2 — [Code] Menu Collector status: surface failure reason + align timeout

- **Domain:** `[Code]`
- **Target Files:** `scripts/crypto-spread-menu.ps1` (Collector status query block)
- **Skill:** `test-driven-development`
- **Description:**
  - Change `Csm-Warn "Collector: Could not query /api/collector/status"` to include `$_` (e.g. `"Collector: Could not query /api/collector/status ($_)`), matching the Trading Engine query pattern at line ~259.
  - Align the Collector query `TimeoutSec` with reliability needs — raise from `3` to `10` (or at minimum `5`) so it is consistent with the now-fast endpoint from T1; keep the Trading Engine timeout unchanged unless needed (out of scope to widen beyond status).
- **Verification:**
  - String-assert test (or `rg` check in PR review) that the collector `catch` block contains `$_` and no longer equals the static-only string; HTML/PS1 content test acceptable.
  - `python -m pytest tests/test_osc_dash_integration.py -q` passes.

### T3 — [Code] Dashboard `refreshCollectorStatus` empty-catch visibility

- **Domain:** `[Code]`
- **Target Files:** `server/osc_dash.py` (`refreshCollectorStatus` JS function)
- **Skill:** `code-review-and-quality`
- **Description:**
  - Replace `}catch{}` / `catch{}` empty block with a logged variant, e.g. `}catch(e){ console.warn('refreshCollectorStatus failed', e); }` (or equivalent that leaves browser console evidence). No change to badge logic on success.
- **Verification:**
  - String-assert test that rendered `GET /` HTML no longer contains the empty `catch{}` at that function and contains a console warning/log path.
  - `python -m pytest tests/test_osc_dash_integration.py -q` passes.

### T4 — [Code] Regression gate + full-suite green

- **Domain:** `[Code]`
- **Target Files:** `tests/test_osc_dash_integration.py` (and any new helper)
- **Skill:** `test-driven-development`
- **Description:**
  - Add the assertions listed in T1–T3 (or augment existing `test_api_collector_status_*` / `test_collector_status_source_matrix` groups) covering the three acceptance criteria: fast status on large file, `$_` in menu failure message, no empty catch in rendered SPA.
  - Run `python -m pytest tests/test_osc_dash_integration.py -q` then `python -m pytest -q` to 0 failures.
- **Verification:** Both targeted and full gates pass; no `skip`/`xfail`/`noqa`.
