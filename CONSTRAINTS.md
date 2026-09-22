# CONSTRAINTS.md — Issue #295: List pristine tick files in the dashboard

Branch: `i295/list-pristine-tick-files-in-the-dashboard` · Size: Small · Type: Code

## Hard boundaries
1. **Zero regressions:** `python -m pytest tests/test_osc_dash_integration.py tests/test_theme_tokens.py -q`
   must pass with the issue's own acceptance tests added. Never the full suite locally (CI gates it).
2. **Error-shape preservation:** existing endpoints' error messages and status codes for invalid/missing
   `file` params are preserved verbatim (existing rejection tests must pass unchanged).
3. **No new dependencies.** stdlib + existing FastAPI only.
4. **Anti-cheat:** no skipping/disabling tests, no deleted assertions, no suppressed linters.
5. **Security:** the resolver is the single path-resolution point — `..`, backslash, absolute paths,
   leading `/`, >2 segments, and non-allow-listed subdirectories are rejected. Containment under
   `TICKS_DIR` is enforced via `relative_to`. Only `pristine` is surfaced; `golden/`, `quarantine/`,
   `.verify_cache/` stay hidden.
6. **Verify-cache correctness:** a pristine sidecar must never be readable as (or mistaken for) a
   top-level day-file sidecar — keys include the relative path; mirrored dir layout on disk.
7. **Scope lock:** no ranking-tier changes, no verify-engine/collector/extractor changes, no delete
   routing for pristine files.
