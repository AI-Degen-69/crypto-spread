# CONSTRAINTS.md — Issue #109 Quality & Architectural Constraints

## 1. Testing & Zero Regressions
- All existing tests stay green: `python -m pytest -q` (394 passed on this
  base). Zero modifications to existing assertions.
- Targeted gate: `python -m pytest tests/test_osc_dash_integration.py -q`
  (extended with new aggregate coverage).
- New tests (red → green): (a) `/api/ticks/manifest` exposes an `aggregate`
  object summing per-file lines/bytes and manifest tape totals; (b) aggregate
  is present (but zeroed/empty) when `run/ticks/` is missing — empty state
  preserved; (c) per-file lines sum equals the aggregate `total_lines` field;
  (d) manifest.json per-series counts are surfaced from cheap sources, not
  recomputed by scanning tick files on every request.

## 2. Anti-Cheat & Integrity
- No disabling, skipping, weakening or deleting existing tests or assertions.
- The flat `files[]` array of `/api/ticks/manifest` keeps its existing fields
  and semantics — the aggregate is purely additive (`aggregate` key added).

## 3. Performance (hard requirement from the issue)
- **No unbounded synchronous full-file scan per page load.** Per-series
  counts must come from cheap sources: the collector's `manifest.json`,
  and/or cached verify reports. File line counts keep the existing estimate
  heuristic (`size/950` for ≥20 MB files); byte totals come from `stat()`.
- A retroactive per-series one-time scan may run only as an explicit cached
  operation (TTL ≥ 10 min), never on every request path by default.

## 4. Dependencies & Scope
- No new external dependencies. No changes under `run/` output format, no
  collector changes beyond what already exists. Backtest/analysis APIs
  untouched. Verify integrity modal untouched (reuse its outputs).

## 5. Verification status (checked Sep 9, 2026 · feat/issue-109-ticks-aggregate)

- `python -m pytest tests/test_osc_dash_integration.py -q` → **47 passed**
  (43 existing + 4 new: aggregate rollup, empty dir, tape from manifest,
  verify→cache→manifest chain).
- `python -m pytest -q` → **398 passed**, zero failures, zero edits to
  existing assertions.
- Performance gate honored: per-series counts come only from verify sidecars
  (`.verify_cache/`) or a TTL-capped (10 min) scan cache; no full-file scan
  on the request path. Line totals reuse the size/950 estimate for ≥20 MB
  files; byte totals come from `stat()`.
