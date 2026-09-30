# Issue #359: Socket Book Divergence Attribution & Defect Localization

## Executive Summary

- **Question to Answer**: Which event type breaks the #174 WebSocket order book?
- **Empirical Verdict**: **100% of order book divergences originate from `price_change` event frames.**
- Full book snapshots (`book`) and trade execution prints (`last_trade_price`) exhibited **0 divergences**.

---

## 1. Quantitative Attribution Findings

An empirical diagnostic capture session was conducted against the live Polymarket CLOB Market WebSocket across all 10 crypto series (20 tokens), yielding 35,333 consecutive events. Replaying each event through `CLOBMarketWSClient` and comparing reconstructed book states against venue ground-truth produced the following attribution breakdown:

| Event Type | Total Dispatched Events | In-Frame Divergences | Reconstructed Book Accuracy | Max Gap Observed |
|---|---|---|---|---|
| `book` (full snapshot) | 508 | **0** | **100.0%** | $0.0000 |
| `last_trade_price` (trade tape) | 244 | **0** | **100.0%** | $0.0000 |
| `price_change` (level deltas) | 34,581 | **114** | **99.67%** (114 corrupted states) | **$0.0500 (5.0¢)** |

Across thousands of market movements, full snapshots always synchronize the book cleanly. The corruption is injected exclusively during subsequent `price_change` stream processing.

---

## 2. Root Cause Mechanism: The `price_change` Level Retention Defect

### The Vulnerability in `apply_price_change` (`strategy/streaming.py:565`)

```python
def apply_price_change(self, token_id: str, side: str, price: float, size: float) -> None:
    with self._state_lock:
        ...
        side_dict = book["bids"] if side.upper() in ("BUY", "BID") else book["asks"]
        if size <= 0:
            side_dict.pop(price, None)
        else:
            side_dict[price] = size

        book["best_bid"] = max(book["bids"].keys()) if book["bids"] else None
        book["best_ask"] = min(book["asks"].keys()) if book["asks"] else None
```

### The Mechanism

1. **Venue Behavior**: When depth at the top of the book changes or sweeps (e.g. ask at 0.43 is filled or cancelled), the Polymarket exchange does not always emit an explicit `{"price": "0.43", "size": "0"}` delta frame. Instead, it emits a `price_change` frame for a new level (e.g. 0.38 BUY, 0.40 BUY), but **explicitly states its current top of book in the frame metadata**:
   ```json
   {
     "price": "0.38", "size": "174", "side": "BUY",
     "best_bid": "0.42", "best_ask": "0.44"
   }
   ```
2. **Local Book Desynchronization**:
   - `apply_price_change` updates price level `0.38`.
   - It recomputes `best_ask` by taking `min(book["asks"].keys())`.
   - Because level `0.43` was never explicitly removed with `size <= 0`, it remains present in `book["asks"]`.
   - `min(book["asks"].keys())` evaluates to **`0.43`**, even though the venue itself declared `best_ask = 0.44`!
3. **Compounding Over Time**:
   As the market trends across minutes, old ghost levels (e.g., bids at 0.70 when the market drops to 0.40) linger in the dictionary, producing the severe **32¢ divergence** observed in Issue #350.

---

## 3. The Smoking Gun Fixture

The first reproducible breaking event sequence has been captured and serialized to:
[`tests/fixtures/socket_divergence_smoking_gun.json`](../tests/fixtures/socket_divergence_smoking_gun.json)

- **Event Index**: #1295
- **Token**: `37405155878908629326136933999342727702461662057689801005175075612557641558269`
- **Reconstructed Book**: `best_bid = 0.42`, `best_ask = 0.43`
- **Venue Truth**: `best_bid = 0.42`, `best_ask = 0.44`
- **Breaking Frame**:
  ```json
  {
    "market": "0x5a9d17dd9cc451fa0f75b8eb029fe3b6835a305befaefe367bb2c0a40692e590",
    "price_changes": [
      {
        "asset_id": "37405155878908629326136933999342727702461662057689801005175075612557641558269",
        "price": "0.38",
        "size": "174",
        "side": "BUY",
        "best_bid": "0.42",
        "best_ask": "0.44"
      }
    ],
    "timestamp": "1790744274443",
    "event_type": "price_change"
  }
  ```

---

## 4. Handoff to Fix Issue

The cause is cleanly isolated. The follow-up fix issue can safely resolve this by:
1. Using in-frame `best_bid` and `best_ask` to prune phantom levels outside the declared spread; OR
2. Pruning bids > in-frame `best_bid` and asks < in-frame `best_ask` whenever a `price_change` frame arrives; OR
3. Resynchronizing on discrepancy.

---

## 5. Post-fix (Issue #362)

The fix took handoff option 2: `apply_price_change` (`strategy/streaming.py`)
prunes bids strictly above and asks strictly below the venue-declared top of book
passed in from `_handle_price_change`, under the existing state lock, before the
single `on_book_update` callback.

```
python scripts/replay_socket_reconciliation.py tests/fixtures/socket_divergence_smoking_gun.json
Replaying extracted fixture object from tests\fixtures\socket_divergence_smoking_gun.json (Issue #362)
Total WS events:            1
Total divergences recorded: 0
price_change       | 1        | 0            | 0          | 0         | N/A       | $0.0000
No divergences detected across the dataset (all books agreed within tolerance).
```

**Residual class (deliberately unfixed):** the prune cannot invent a level the venue
declares but the local ladder never received. A frame declaring a best that is absent
locally still records an in-frame divergence — see
`test_reconciler_detects_a_level_missing_from_local_depth`. The fix therefore narrows
the #174 divergence to "lost frames", it does not prove the upstream cause of the
missing deletions. A fresh #174 `book_shadow` measurement (replay purity aside) is
still required before any Phase 2 switch.
