# Historical Maker Strategy Rationale & Measurements

> [!WARNING]
> **HISTORICAL / ARCHIVAL DOCUMENTATION ONLY**
> The values and rationale below are historical measurements from a retired hunter-fleet liquidity rewards experiment (July 2026).
> They do **not** configure or govern the live trading engine or the backtest engine.
>
> **Configuration Owners:**
> - **Live / Paper Trading:** `LiveTraderEngine.__init__` in [`strategy/live_trader.py`](file:///strategy/live_trader.py) owns live execution defaults (e.g., `max_pair_cost = 0.99`).
> - **Backtesting & Simulation:** `BacktestParams` in [`backtest/engine.py`](file:///backtest/engine.py) owns replay and parameter sweep configuration.

---

## 1. Provenance & Dataset

The empirical values recorded below originated from analysis of `powerwinner`'s market-making activity on Polymarket:
- **Sample:** 56,768 BTC/ETH 5-minute fills over 2026-07-14 to 2026-07-21 (2,970 discrete markets).
- **Primary Source:** `research/powerwinner_analysis.md` and legacy `strategy/config.py`.

---

## 2. Objective Tradeoffs: "Pair" vs "Rewards"

### The "Pair" Objective
- Rest quotes under the ask to buy a hedged pair for under \$1.00.
- **Measured Failure:** Quoting off the ask placed every quote approximately half a spread above mid, making the pair cost \$1.00 + spread by construction. Order books traded at ~101% throughout the window, leaving negative gross capture.

### The "Rewards" Objective
- Quote for the Polymarket liquidity-reward score rather than for the fill.
- Markets were incentivized under Gamma with parameters such as:
  - `rewardsMaxSpread = 0.045` (4.5¢)
  - `rewardsMinSize = 50` shares
  - `feeSchedule.rebateRate = 0.20` (20% of taker fees redistributed daily)
- Reward score formula on resting size (filled or unfilled):
  $$S(v, s) = \left(\frac{v - s}{v}\right)^2 \times \text{size}$$
  where $v = 4.5¢$ and $s$ is own spread.
- **Key Learnings:**
  1. Sitting out was expensive: skipping cycles earned zero score.
  2. Quoting off mid made pair cost $1.00 - 2 \times \text{offset}$, automatically pricing under \$1.00.

### Reward Offset
- `reward_offset = 0.020` (2.0¢): Score was quadratic in closeness (0.5¢ yielded ~16% of market score, 2.0¢ yielded ~7% over 3,906 recorded legs). Closer resting earned higher scores but suffered increased adverse selection.

---

## 3. Inventory Skew (Replacing Settlement Cross-Hedge)

- **Failure of Settlement Cross-Hedge:** Crossing the spread near settlement close executed only after the outcome was virtually decided (measured hedge prices: 0.01, 0.02), booking luck as profit and failing to protect downside.
- **Inventory Skew:** When holding an unhedged/naked leg (e.g., long UP), quote UP farther away from mid and DOWN closer to mid to attract balancing fills while both sides still hold value.

---

## 4. Exposure & Risk Caps: Dollar-Based vs Share-Based

- Legacy Caps:
  - `max_naked_usd = 120.0`
  - `max_fleet_naked_usd = 400.0`
  - `max_committed_usd = 1000.0` (or bankroll)
- **Why Share Caps Failed:** A share cap (e.g. 233 shares) failed because share price varies from 0.01 to 0.99. 200 shares at 0.90 is \$180 at risk, whereas 200 shares at 0.05 is \$10. Caps must be enforced in **dollars**, not share counts.

---

## 5. Market Gates & Filters

- **Price Band Gate:** `0.10` to `0.90`. Outside this range, the market is approaching certainty; adverse selection spikes.
- **Decided Price:** `0.02`. Do not quote when outcome probability is practically binary.
- **Book Spread:** `max_book_spread = 0.06` (6¢). Wider books indicate insufficient liquidity or extreme volatility.
- **Minimum Depth:** `min_book_depth_sh = 200` shares.
- **Time Remaining Gate:** `min_t_remaining_sec = 15.0` seconds. Late entries cannot exit or balance before settlement.

---

## 6. Execution Latency & Venue Acceptance

Measured latencies from hunter-fleet deployment:
- **Median Venue Acceptance:** `post_venue_accept_ms = 81.0` ms.
- **Network One-Way:** `net_oneway_ms = 3.93` ms.
- **Implication:** Safe for resting limit orders (GTC) in 300-second windows, but taker / IOC orders must account for venue matching delays.

---

## 7. Pairs Exit Discipline & Historical Pair Cost

- **Pairs Exit Pattern:** Gain +3.68¢ / cost -3.67¢ measured on rule-era data with a 900-second exit window.
- **Historical vs Engine Default `max_pair_cost`:**
  - In `MakerConfig`, `max_pair_cost` was set to `0.995` (historical hunter-fleet threshold).
  - In `LiveTraderEngine`, the true canonical trading default is **`0.99`** (`strategy/live_trader.py:920`).
  - Merging gas: ~$0.05 per transaction (conservative Polygon CTF merge fee estimate), meaning single-share pair merges were uneconomic.
