Branch: i452/fix-paper-stop-loss-note-claims-drift-breach-when | Issue: #452

# Implementation Plan — Stop-Loss Note Names the Condition That Fired

## Size & Stack
- Tier: **Small** — note construction in two trigger blocks of one function (`strategy/live_trader.py:4942-4990`) plus regression tests; single decision (inline local booleans + drift precedence, no helper).
- Task type: **Debug + Code**. Stack: Python, pytest (`tests/test_live_trader.py` + neighbor `tests/test_stop_orders.py`). No UI, no API, no dependency change. No CI gate (workflow deleted per operator order) — targeted local suites only.

## CodeRabbit Intake Note
- Adopted: 2-phase skeleton (note selection per trigger + 4 regression tests with exact fixtures); seam pointers (UP 4942–4964, DOWN 4968–4990, stop staging formula, demo note 3670, dashboard fallback 7114–7115); precedence rule (drift wins ties); inline-over-helper choice.
- Rejected: over-split Phase/Task ceremony — merged into 4 atomic tasks (UP, DOWN, tests, verification); the stale "do not change CONSTRAINTS.md content for #449" instruction (that file targets #451 now, and retargeting it to #452 is this station's standing practice).
- `[UNVERIFIED]` at intake: none left — every cited seam spot-checked (trigger blocks verbatim; stop formula `fill_price - exit_thresh` at 1324; demo note at 3670; dashboard `notes.includes('UP'/'DOWN')` at 7114–7115; repo-wide grep proves no code parses the "Adverse drift" prefix; only one existing test asserts stop-note content, line 2984).

## Resolved Open Questions (from code, not asked)
- **No `needs-answers` label, no Open questions section** — the issue names both blocks, the pattern, and the gate. Nothing to ask.
- **Precedence on simultaneous fire:** drift breach wins. Evidence: the issue requires genuine drift breaches to keep the current format, and the drift claim is true when both hold. This also keeps `test_naked_leg_stops_at_exit_thresh` (bid 0.45 hits staged stop 0.45 AND the test's own comment claims drift 0.04 ≥ 0.03) green without touching it.
- **Blast radius on existing assertions:** repo-wide grep for `.notes` in tests finds 12 hits; only line 2984 (`"0.03" in notes`) touches stop-note content — the rest are dead-zone/exit-bid/unavailable notes. The staged-stop note keeps `:.2f` threshold formatting so 2984 passes either way.
- **type-design-analyzer / code-explorer:** skipped — no interface change, path traced directly in `live_trader.py`.

## Spec (embedded — Small tier)
- Goal: a staged-stop fire below the drift threshold logs a staged-stop note, never an "Adverse drift" claim; a genuine drift breach keeps the current format.
- Acceptance: (1) staged-stop-only fires (UP + DOWN) produce a note with side + threshold and no "Adverse drift"; (2) drift-only and simultaneous fires keep the exact "Adverse drift X >= Y" format; (3) `python -m pytest tests/test_live_trader.py -q` green with no regressions.
- Out of scope: trigger thresholds, dead-zone exits (#451 done), fill rules, live venue flow, dashboard rendering, demo note 3670, the 4925 fallback call.

## Improvement Proposal (adopted — edge-case hardening)
- Evidence (code): `assert "0.03" in engine.trades[-1].notes` (`tests/test_live_trader.py:2984`) — this passes under both the buggy and fixed behavior (both note shapes contain "0.03"), so the existing test cannot catch this bug class.
- Proposal: pin that fixture's actual note shape with one companion assertion (Station III reads the run: drift-breach → assert exact drift-note equality; staged-stop-only → assert no "Adverse drift"). Adopted into Task 3 — hardens the nearest existing test instead of adding scope.

## Tasks

### [x] Task 1: [Debug] Note selection in the UP trigger (`strategy/live_trader.py:4942-4964`) (S)
- **Files:** `strategy/live_trader.py` (UP stop block only)
- **Depends on:** none (riskiest wording decision — the staged-stop note shape — goes first)
- **Description:** compute local `drift_breach_up = filled_up and not filled_down and max_down_drift >= exit_thresh`; use it in place of the inline expression (keep OR with `paper_stop_hit_up` and all guards); capture stop price to a local before the metadata clear (clear order/fields unchanged); drift true → existing note text byte-identical; drift false → staged-stop note with side "UP", touching bid, captured stop price, actual drift, threshold `:.2f`, no "Adverse drift", no ">=" between drift and threshold.
- **Skill:** `debugging-and-error-recovery`
- **Verification:** Task 3 UP tests fail before / pass after; trigger condition truth table unchanged (same fires, different words).

### Task 2: [Debug] Mirror note selection in the DOWN trigger (`strategy/live_trader.py:4968-4990`) (S)
- **Files:** `strategy/live_trader.py` (DOWN stop block only)
- **Depends on:** Task 1 (note shape locked once, mirrored once)
- **Description:** same change with `filled_down`/`filled_up`/`max_up_drift`/`paper_stop_hit_down`/`down_bid`, side text "DOWN". Leave demo note 3670 untouched.
- **Skill:** `debugging-and-error-recovery`
- **Verification:** Task 3 DOWN test fail-before/pass-after; symmetry with Task 1 by inspection.

### Task 3: [Debug] Note-selection regression tests in `tests/test_live_trader.py` (S)
- **Files:** `tests/test_live_trader.py` (near `test_naked_leg_stops_at_exit_thresh`, line 2957)
- **Depends on:** Task 2
- **Description:** (a) staged-stop-only UP (thresh 0.05, fill `(0.47,0.479,0.51,0.52)`, exit `(0.43,0.44,0.53,0.54)` → drift 0.030 < 0.05, bid touches stop 0.43) and DOWN mirror — assert no "Adverse drift", side + threshold present; (b) drift-only (thresh 0.03, exit `(0.46,0.47,0.59,0.60)` → drift 0.045, bid above stop 0.45) assert exact "Adverse drift 0.045 >= 0.03"; (c) simultaneous (thresh 0.05, exit `(0.43,0.44,0.59,0.60)`) assert drift format (precedence lock); (d) harden line 2984 with one companion assertion pinning that fixture's actual note shape. Setup mirrors neighbors (`_naked_market`, `_open_50_50_quotes`, paper mode, `load_persisted=False`). Weaken nothing.
- **Skill:** `test-driven-development`
- **Verification:** new tests fail on pre-fix notes, pass after Tasks 1–2.

### Task 4: [Backend/Logic] Targeted verification sweep (XS)
- **Files:** none (verification only)
- **Depends on:** Task 3
- **Description:** run `python -m pytest tests/test_live_trader.py -q` and `tests/test_stop_orders.py -q`. No full-suite local run; no CI gate exists. Confirm glossary terms in comments ("trading engine", "paper mode").
- **Skill:** `incremental-implementation`
- **Verification:** both suites green; branch clean except the two intended files.

## Checkpoints
- After Task 1: UP fires word truthfully; nothing else in the engine changed.
- After Task 3: all four note paths locked by fail-before/pass-after tests — ready for `iii-build-plan` handoff review.
