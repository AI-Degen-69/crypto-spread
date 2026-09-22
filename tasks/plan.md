# Plan — Issue #294: Rank the least-bad tick file as preferred when no file fully qualifies

Branch: `i294/rank-least-bad-tick-file-preferred` | Issue: #294

Stack: Python 3 + FastAPI, pytest · Size: **Standard** (one module + its endpoint/UI tests;
2–3 files, one ranking decision) · Type: **Code** (Backend/Logic + light UI)

## Resolved inputs (planning record)
- Issue supplies exact acceptance criteria, scope, and relevant files. No `needs-answers`
  label, no open questions → nothing to resolve from the operator.
- `code-explorer` persona skipped: not Large/unfamiliar code — the issue names every reuse
  point with file:line.
- `type-design-analyzer` persona skipped: the ranking contract is an extension of the
  existing `pick_preferred()` pure function, same interface shape.
- Sub-issue mapping skipped: 4 linear tasks stay tracked here + `tasks/todo.md` only.

## Prior work
Issue #279 (PR #293) shipped `pick_preferred()` with tier-1 eligibility
(`PASS` + `COMPLETE CAPTURE`). The function, tests, badge, and pre-select all exist and work.
This issue extends the ranking with a tier-2 fallback — minimal changes to the same function.

## Spec
See `SPEC.md`. One-line summary: when no file meets the PASS+COMPLETE bar, rank all files
by a defined total order (integrity > capture > readiness > windows > mtime) and star the
least-bad one with a "Best available" badge instead of "Preferred".

## Interface contracts (frozen before logic)
- `pick_preferred(files) -> (dict, int) | (None, None)` — returns the winning entry **and
  the tier** (1 or 2), or `(None, None)`. Tier 1: unchanged PASS+COMPLETE rule. Tier 2:
  total order over all files (see SPEC.md). Still pure — no I/O, no globals.
  *Alternative*: return a dict `{"winner": dict, "tier": int}` or keep returning `dict | None`
  and derive tier from the winner's fields at the call site — **chosen: return tuple** for
  minimal API surface; the endpoint destructures into `preferred_file` + `preferred_tier`.
- Endpoint payload additions (additive only): `out["preferred_tier"]` = 1 | 2 | `null`.
  Existing fields `preferred_file` and per-file `is_preferred` keep the same semantics but
  may now be non-null when only tier-2 files exist.
- Badge wording: tier 1 → `"★ Preferred"` (unchanged); tier 2 → `"★ Best available"`.
  Tooltip changes to reflect that the star is a least-bad pick, not a full qualification.
- Dropdown: same ★ prefix; no label change needed (star already tells the story; the badge
  row in the table is the verbose distinction).

## Tasks
- **TASK-1** [Backend/Logic] · Size S · `server/osc_dash.py`
  Extend `pick_preferred()` to return `(winner, tier)` tuple. Tier 1 = current rule
  (PASS+COMPLETE). Tier 2 = `max()` over all files with the defined total order
  (integrity_rank, capture_rank, readiness_rank, windows_count, mtime). Wire
  `preferred_tier` into `api_ticks_manifest()`. Update call site to destructure.
  · Depends on: — · Verify: targeted pytest (update existing pure-ranking tests + add
  tier-2 tests).

- **TASK-2** [Backend/Logic] · Size S · `tests/test_osc_dash_integration.py`
  Add tier-2 test cases: all-PARTIAL → tier-2 winner picked with correct
  `preferred_tier: 2`; mix of PASS+COMPLETE and PARTIAL → tier-1 still wins; tier-2
  total order: PASS > WARN on integrity, COMPLETE > PARTIAL on capture, then readiness,
  windows, mtime. Update existing `test_pick_preferred_pure_ranking` to expect tuple
  return. Keep all existing tier-1 endpoint tests green.
  · Depends on: TASK-1 · Verify: targeted pytest.

- **TASK-3** [Design/UI] · Size XS · `server/osc_dash.py` (badge rendering + dropdown)
  Modify badge text: tier 1 → "★ Preferred", tier 2 → "★ Best available". Tooltip
  updated. Dropdown star prefix unchanged. Pass `preferred_tier` through to JS.
  Update `tests/test_theme_tokens.py` badge assertion.
  · Depends on: TASK-1 · Verify: targeted pytest + browser preview.

- **TASK-4** [Backend/Logic] · Size XS · closeout
  Run the full targeted gate `python -m pytest tests/test_osc_dash_integration.py
  tests/test_theme_tokens.py -q`; tick todos; confirm nothing else changed
  (`git diff --stat`). · Depends on: TASK-1..3 · Verify: targeted pytest green.

Checkpoints: after TASK-2 (backend contract + all tests green), after TASK-3 (UI visible).

## Improvement proposal (recorded)
- **Adopted (simplification):** return a `(winner, tier)` tuple from `pick_preferred()`
  rather than adding a separate `pick_tier2()` function — evidence: the issue says *"tiered
  eligibility in `pick_preferred()`"*, meaning the tiers live inside the same function, not
  beside it. One function, one total order, two tiers. Minimal diff, no new helpers.
