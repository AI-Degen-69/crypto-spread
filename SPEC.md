# SPEC.md — Issue #207: An unpriceable leg is substituted with 0.50 instead of skipping the window

## 1. Problem Statement

When a leg's order book is empty, missing, or one-sided, the live trading engine calls `book_math.two_sided_mid_with_default(..., default=0.50)` in two places (`strategy/live_trader.py:2175`, `:4369`).
This substitutes a fabricated synthetic mid of `0.50` for an unpriceable book.
Downstream, this fabricated 0.50 creates serious defects:
1. **Adverse-open gate** (`:4540`): `abs(0.50 - 0.50) = 0`, so it passes unconditionally on an empty book that never priced anything.
2. **Entry band** (`:4563`): `abs(0.50 - 0.50) = 0`, so it passes unconditionally.
3. **Drift tracking** (`:4456`): treats the market as flat at 0.50, ignoring actual movement or masking real risk.
4. **Order quoting** (`:4403`): if delay expires and `mstate.mid` is 0.50, the engine can quote orders blind at `0.50 - offset` into an unpriced market.
5. **Operator blindness**: the Live Cockpit displays `mid: $0.50` and a flat line, hiding the fact that the venue book was missing or dead.

## 2. Specification & Contracts

### 2.1 Use Honest Mid Calculation
- In `strategy/live_trader.py`, replace calls to `book_math.two_sided_mid_with_default` with `book_math.two_sided_mid`.
- `two_sided_mid` returns `Optional[float]` (`None` if any leg lacks a valid bid or ask).
- `mstate.mid` retains its type `Optional[float] = None`.

### 2.2 Quoting & Execution Safety
- Do not quote when `mstate.mid is None`. Add `mstate.mid is not None` to `can_place_entry`.
- When `mstate.mid is None` and not already quoting/filled:
  - If entry delay has expired, set `mstate.status = "NO_BOOK"` and `mstate.last_action = "Waiting for two-sided book (unpriceable leg) — quoting held"`.
- If the entry window expires/times out and no two-sided book was ever formed (`open_gate_evaluated` is False and `mstate.mid is None`):
  - Mark window status as `"NO_BOOK_SKIPPED"`.
  - Set `mstate.last_action = "Window skipped — unpriceable book (never formed two-sided quotes)"`.
  - Do not record as standard `TIMEOUT_NO_FILL`.

### 2.3 Drift Tracking Safety
- In drift tracking (`strategy/live_trader.py:4455`), only evaluate drift and reversal detection when `mstate.mid is not None`.
- Never execute drift logic against an assumed `0.50` fallback.

### 2.4 Cockpit & UI Integration
- In `server/osc_dash.py`:
  - Add `'NO_BOOK': 'No Book'` and `'NO_BOOK_SKIPPED': 'No Book Skipped'` to `OT_STATUS_LABELS`.
  - Update `bidsCancelled` and `cancelReason` to handle `'NO_BOOK_SKIPPED'`.
  - Display appropriate status tags in cockpit cards.

### 2.5 Acceptance Criteria
1. When either leg is unpriceable, `mstate.mid` is `None`.
2. No resting orders are placed while `mstate.mid is None`.
3. No drift accumulation occurs against an unpriceable leg.
4. Windows that timeout with no book receive status `"NO_BOOK_SKIPPED"`.
5. All targeted tests pass.
