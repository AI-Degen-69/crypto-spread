Branch: i456/feat-quoting-gate-order-prices-against-quote-range | Issue: #456

# Implementation Plan — quote_range Guards Order Prices

## Size & Stack
- Tier: **Standard** — one latch decision in two engine files (`strategy/live_trader.py:4250-4258`, `backtest/engine.py:1139-1151`) plus regression + parity tests; single architectural decision (latch-time pair rejection, mirrored for parity).
- Task type: **Code**. Stack: Python, pytest (`tests/test_live_trader.py`, `tests/test_quote_range_parity.py`, `tests/test_backtest_engine.py`). No UI, no API, no dependency change. No CI gate (workflow deleted per operator order) — targeted local suites only.

## CodeRabbit Intake Note
- No plan comment received yet (only the request prompt) — nothing adopted, nothing rejected.
- `[UNVERIFIED]`: none — all seams read directly from live code (see Resolved Open Questions).

## Resolved Open Questions (from code, not asked)
- **No `needs-answers` label, no Open questions section** — the issue prescribes latch-time pair rejection in both engines. Nothing to ask.
- **Enforcement point:** latch time (after both legs computed, before anchoring). Evidence: the live latch comment states "Range and dead zone gate the latch itself" (`live_trader.py:4246-4248`) — leg gating extends the documented design instead of inventing a new one.
- **Parity surface:** both engines share the mid-gate + leg formula (`engine.py:1142` + `:1150` mirror `live_trader.py:4255` + `:4257-4258`); `tests/test_quote_range_parity.py` (T3) plus `tests/test_engine_parity.py` pin parity — the change lands in both or neither.
- **Mid-gate stays:** the mid preconditions (4253-4255 / 1139-1142) are untouched; the leg check is an additional conjunct, so in-range behavior is byte-identical by construction.
- **type-design-analyzer / code-explorer:** skipped — no interface change, latch path traced directly in both engines.

## Spec
See `SPEC.md` (Standard tier): pair rejection on out-of-range legs, inclusive boundary, in-range byte-identical behavior, edge cases (clamped values, lone-leg ban, dead-zone interplay, chase untouched), out of scope.

## Improvement Proposal (adopted — edge-case hardening)
- Evidence (code): "`if quote_lo <= mstate.mid <= quote_hi and not self.quoting_halted:`" (`strategy/live_trader.py:4237`) and "`and quote_lo <= anchor_mid <= quote_hi`):" (`backtest/engine.py:1142`).
- Proposal: legs exactly at lo/hi quote normally (inclusive `<=` on both ends), mirroring both mid gates — so a leg at exactly 0.10 or 0.90 is quotable. Adopted into Tasks 1–2 (hardening, not scope expansion: it fixes the boundary the new check introduces).

## Tasks

### [x] Task 1: [Backend/Logic] Leg gate in the live latch (`strategy/live_trader.py:4250-4258`) (S)
- **Files:** `strategy/live_trader.py` (anchor latch block only)
- **Depends on:** none (riskiest wording — the rejection condition — goes first)
- **Description:** after computing resting_up/down, if either falls outside `quote_range` (inclusive), latch nothing: leave `anchored_mid` None and set neither resting price, so no orders place that tick and the next tick retries. In-range path byte-identical.
- **Skill:** `test-driven-development`
- **Verification:** Task 3 live tests fail before / pass after; mid-gate-only behavior unchanged.

### Task 2: [Backend/Logic] Mirror leg gate in the backtest latch (`backtest/engine.py:1139-1151`) (S)
- **Files:** `backtest/engine.py` (anchor latch block only)
- **Depends on:** Task 1 (condition shape locked once, mirrored once)
- **Description:** same conjunct on the computed resting_up/down with inclusive bounds. No other backtest logic touched.
- **Skill:** `test-driven-development`
- **Verification:** Task 3 backtest tests fail before / pass after; parity suite green.

### Task 3: [Backend/Logic] Rejection + parity regression tests (S)
- **Files:** `tests/test_live_trader.py`, `tests/test_quote_range_parity.py` (and backtest engine suite if that is where quoting tests live)
- **Depends on:** Task 2
- **Description:** (a) live: mid 0.19/offset 0.15 latches nothing, no order handles, retry next tick quotes when mid returns in-range-legged; (b) backtest mirror of (a); (c) boundary: legs exactly at lo/hi quote; (d) lone-leg ban: one leg out → neither places; (e) in-range control unchanged. Weaken nothing.
- **Skill:** `test-driven-development`
- **Verification:** new tests fail pre-fix, pass post-fix.

### Task 4: [Backend/Logic] Targeted verification sweep (XS)
- **Files:** none (verification only)
- **Depends on:** Task 3
- **Description:** run `tests/test_live_trader.py`, `tests/test_quote_range_parity.py`, `tests/test_backtest_engine.py` (plus `test_engine_parity.py` if touched). No full-suite local run; no CI gate exists. Confirm glossary terms in comments ("trading engine", "paper mode").
- **Skill:** `incremental-implementation`
- **Verification:** all suites green; branch clean except intended files.

## Checkpoints
- After Task 1: out-of-range mids place nothing live; in-range byte-identical.
- After Task 3: rejection + parity locked by fail-before/pass-after tests — ready for `iii-build-plan` handoff review.
