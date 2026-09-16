# SPEC.md — Issue #216: Dashboard resting-price fallback reads up_mid/down_mid

## 1. Problem Statement

In `server/osc_dash.py`, six sites fall back to `m.up_mid` / `m.down_mid` when `resting_up` / `resting_down` are null:
- Toast notifications for UP/DOWN fill transitions (`:3327`, `:3337`)
- Orders & Position status display string (`:6016`, `:6017`)
- Market matrix bids display (`:6066`, `:6067`)

Neither `up_mid` nor `down_mid` is ever emitted in any payload from the live trader. As a result:
1. The `m.up_mid` / `m.down_mid` branch is completely dead and unreachable.
2. The chain unconditionally falls through to hardcoded `0.48` and hardcoded `offset = 0.02`.
3. An operator running with a non-default offset (e.g., `offset = 0.03`) sees `0.48` displayed instead of their actual strategy parameter (`0.47`), distorting operator telemetry.

## 2. Specification & Contracts

### 2.1 Shared Helper Functions in Dashboard JavaScript

Introduce two cohesive, shared helper functions in the client script of `server/osc_dash.py`:

```javascript
function cockpitRestingPrice(m, leg, fallbackOffset) {
  const isUp = leg === 'up';
  const resting = isUp ? m?.resting_up : m?.resting_down;
  if (resting != null) return resting;
  const off = (fallbackOffset != null) ? fallbackOffset : 0.02;
  const mid = m?.mid;
  if (mid != null && isFinite(mid)) {
    const anchor = isUp ? mid : (1.0 - mid);
    return Math.max(0.01, +(anchor - off).toFixed(2));
  }
  return Math.max(0.01, +(0.50 - off).toFixed(2));
}

function cockpitLegPrice(m, leg, fallbackOffset) {
  const isUp = leg === 'up';
  const fill = isUp ? m?.fill_price_up : m?.fill_price_down;
  if (fill != null) return fill;
  return cockpitRestingPrice(m, leg, fallbackOffset);
}
```

### 2.2 Call Site Replacements

1. **Fill Toast Transitions (lines ~3327, ~3337):**
   - Replace ternary chain with `cockpitLegPrice(m, 'up', st?.params?.offset)` and `cockpitLegPrice(m, 'down', st?.params?.offset)`.
2. **Card Positions PosStr (lines ~6016, ~6017):**
   - Replace ternary chain with `cockpitLegPrice(m, 'up', st?.params?.offset)` and `cockpitLegPrice(m, 'down', st?.params?.offset)`.
3. **Card Bids Display (lines ~6066, ~6067):**
   - Replace ternary chain with `cockpitRestingPrice(m, 'up', st?.params?.offset)` and `cockpitRestingPrice(m, 'down', st?.params?.offset)`.

### 2.3 Acceptance Criteria

1. No references to `up_mid` or `down_mid` remain in `server/osc_dash.py`.
2. When `resting_up` / `resting_down` are null and `st.params.offset` is 0.03 (with no `mid`), the resting price evaluates to `0.47`, never `0.48`.
3. When `resting_up` is null, `st.params.offset` is 0.03, and `mid` is 0.60, UP evaluates to `0.57` and DOWN evaluates to `0.37`.
4. Existing tests pass, and new integration tests in `tests/test_osc_dash_integration.py` verify points 1-3.
