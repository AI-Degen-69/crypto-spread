# Plan — Issue #206: Live entry quotes are always 0.50 - offset

- **Issue:** #206 (`ready-for-agent`, assigned)
- **Branch:** `fix/live-entry-anchor-mid-206`
- **Size tier:** Small — one block in one function, one file plus its tests.
  It is small in diff and large in consequence: it is the pricing of every live
  entry order the engine places.
- **Task type:** Debug (root-cause of a live-money defect) + Code.
- **Stack:** Python 3.12.10, FastAPI project, pytest. No venv activation needed.
- **Skills routed:** `debugging-and-error-recovery` (root cause is already
  isolated — the `locals()` guard), `test-driven-development` (red before green),
  `source-driven-development` (the correct formula is already in the repo twice).
  No UI skills: this is engine logic, nothing renders.

## Root cause (confirmed by reading, not assumed)

`strategy/live_trader.py:4384-4385` reads `up_mid` / `down_mid`. Neither name is
bound anywhere in `_update_market_strategy` or its module scope — the only
`up_mid` in the repo is a local inside `book_math.two_sided_mid` (`book_math.py:89`).
So `'up_mid' in locals()` is permanently `False` and both legs price off `0.50`.

The correct value exists already: `mstate.mid` is assigned at `:4365` from
`two_sided_mid_with_default`, and the exact formula to apply to it exists twice:

- re-quote path, `live_trader.py:5190`: `anchor_up = round(min(0.99, max(0.01, mid - self.offset)), 3)`
- backtest, `backtest/engine.py:786-788`: same, with `(1.0 - _anchor_f)` for the down leg.

`tests/test_live_trader.py:2831` (`test_requote_dynamic_anchor_math`) already
pins that behaviour — **for round 1 only**. Round 0 was never covered, which is
how the dead fallback survived.

## Tasks

### T1 — Branch and red test `[Debug]`
- Create `fix/live-entry-anchor-mid-206` off `master`.
- Add `test_initial_entry_anchors_to_live_mid` to `tests/test_live_trader.py`,
  next to `test_requote_dynamic_anchor_math` so the round-0 and round-1 cases
  read as a pair.
- Model it on that test's fixtures: a 15m engine, a benign 0.50 open snapshot so
  the adverse-open gate (`:4509`) stays out of the way, then a skewed two-sided
  book with mid `0.60`.
- Assert `m.resting_up == 0.57` and `m.resting_down == 0.37` at `offset = 0.03`
  (or the engine's configured offset, expressed as `round(0.60 - engine.offset, 3)`).
- **Verification:** `python -m pytest tests/test_live_trader.py -q -k initial_entry_anchors`
  must FAIL, reporting `0.47`. Capture that output — CONSTRAINTS §5 requires it.

### T2 — The fix `[Backend/Logic]`
- `strategy/live_trader.py:4383-4387`: delete the two `locals()` lines and price
  from the mid:

```python
_anchor = mstate.mid if mstate.mid is not None else 0.50
resting_up = round(min(0.99, max(0.01, _anchor - self.offset)), 3)
resting_down = round(min(0.99, max(0.01, (1.0 - _anchor) - self.offset)), 3)
```

- Update the comment above it: it currently describes behaviour the code did not
  have. Name `mstate.mid` and say the price is recomputed every tick until an
  order exists, which is what makes it placement-time fresh.
- **Verification:** the T1 test goes green; `tests/test_live_trader.py` whole file green.

### T3 — Boundary and parity tests `[Debug]`
- Add to the same test, or a sibling, the two cases that keep the fix honest:
  - pair-sum invariant: `resting_up + resting_down == round(1 - 2*offset, 3)`
    across several mids (0.20 / 0.50 / 0.80).
  - clamping: a mid at 0.02 with `offset = 0.03` must not emit a price below 0.01.
- Assert the mid-0.50 case still yields the historical `0.47` / `0.47`, so the
  claim "no existing fixture changes" is a test, not a hope.
- **Verification:** `python -m pytest tests/test_live_trader.py tests/test_entry_timeout.py tests/test_patient_band_preset.py -q`.

### T4 — Placement-time proof `[Debug]`
- One test with `entry_delay_sec = 60`: feed a tick at mid 0.50 before the delay
  expires (no order placed), then a tick at mid 0.65 after it expires, and assert
  the placed price is `0.65 - offset`, not `0.50 - offset`.
- This is the operator's own acceptance criterion from the #206 comment; without
  it the fix is correct by inspection only.
- **Verification:** same command as T3.

### T5 — Document the pre-quote exception `[Docs]`
- `strategy/live_trader.py:4230`: add one comment line saying the `0.50` there is
  deliberate — the T+1 window has no book to anchor to, and the block is already
  suspended whenever `entry_delay_sec` or `entry_band` is armed. No code change.
- **Verification:** `python -m pytest tests/test_docstrings.py -q`.

### T6 — Full suite and PR `[Backend/Logic]`
- `python -m pytest -q` (~50s).
- Commit per task, conventional, scoped `fix(live)`. PR body carries the red
  output from T1 and the green output from T6, per CONSTRAINTS §5, and states
  plainly that every backtest result for a non-zero `entry_delay_sec` before this
  commit described a strategy the live engine never ran.

## 💡 Proposed improvement (operator decides — not folded in)

The clamp formula now appears three times: round-0 (after T2), the re-quote path
(`:5190`), and the backtest (`engine.py:786`). A four-line module function —

```python
def anchor_prices(mid: float, offset: float) -> tuple[float, float]:
    """Both legs' resting prices at `mid`: (mid - offset, (1 - mid) - offset)."""
```

— in `strategy/book_math.py`, called from both live sites, would make the two
live copies impossible to drift apart again, and gives #214's parity harness a
single symbol to assert against. It is a real deduplication of code that already
exists, not speculative abstraction.

**Cost:** touches the re-quote path, which is currently green and out of scope,
so it widens the blast radius of a live-money fix. **Recommendation: defer to
#214**, where parity is the whole point. Adopt now only if you want it.

## Ordering note

#206 is the unblocker. #213 (entry band) must land after it — relaxing the band
while quotes are still nailed to `0.50 - offset` quotes 0.47/0.47 into a market
at 0.70, which is exactly the adverse-fill scenario with the guard removed.
#209 (stop measured from 0.50) and #214 (parity) both read this issue's result.
