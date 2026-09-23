# Jungle King: Golden-Dataset OFAT Parameter Range Manifest

`research/jungle-king/` is the foundation home for evolutionary optimization ("survival of the fittest") on the certified golden dataset (`docs/golden-tick-dataset.md`, 6 days, 4,910 windows, `RESEARCH_READY`).

## The OFAT Methodology

All primary sweeps follow the **One-Factor-at-a-Time (OFAT)** rule:
> **Vary one parameter across its declared range while holding all other parameters fixed at their baseline.**

This isolates the marginal effect of each knob, mapping the sensitivity curve and locating inflection points, local extrema, and parameter cliffs without combinatorial explosion.

## Baseline Reference Definition

The baseline represents the battle-tested, live-replicable configuration combining `BacktestParams` and `scripts/backtest.py` defaults:

| Parameter | Baseline Value | Class / Source | Description |
|---|---|---|---|
| `offset` | `0.020` ($0.02 / 2¢) | Tuning knob | Spread offset placed from the two-sided mid |
| `queue_gate` | `50.0` shares | Tuning knob (CLI default) | Minimum queue depth ahead before placing quote |
| `quote_shares` | `120` shares | Tuning knob (CLI default) | Order share sizing per leg |
| `entry_delay_sec` | `0.0` s | Tuning knob | Initial window wait before quoting begins |
| `entry_delay_pct` | `0.0` % | Tuning knob | Relative window fraction delay before quoting |
| `exit_reversal` | `0.020` ($0.02 / 2¢) | Tuning knob | Reversal buffer toward 0.50 cancelling stop loss |
| `exit_thresh_by_slug.*` | `0.050` ($0.05 / 5¢) | Tuning knob | Stop-loss exit delta across all 5m & 15m series |
| `enable_leg_chase` | `False` | Tuning knob | Re-anchor second leg after first fills |
| `max_pair_cost` | `0.99` ($0.99) | Structural limit | Maximum allowed cost for completed binary pair |
| `quote_range` | `[0.10, 0.90]` | Structural limit | Two-sided mid quotable interval (inclusive) |
| `dead_zone_val` | `0.10` (10%) | Structural limit | Untradeable tail at window expiration |
| `dead_zone_unit` | `pct` | Structural limit | Unit of dead zone measurement |
| `naked_leg_at_expiry` | `close` | Structural limit | Close unpaired leg at book bid in dead zone |
| `taker_fee_rate` | `0.07` | Execution assumption | Crypto taker fee coefficient |
| `merge_gas_usd` | `0.0` ($0.00) | Execution assumption | Sponsored/gasless position merge cost |
| `tick_size` | `0.001` ($0.001) | Execution assumption | Price tick grid granularity |
| `min_quote_shares` | `5` shares | Execution assumption | Exchange minimum order size |

---

## Parameter Candidate Ranges (Checklist Manifest)

### 1. Trading Tuning Knobs

#### Spread Offset (`offset`)
- [ ] 0.001 (0.1¢)
- [ ] 0.002 (0.2¢)
- [ ] 0.005 (0.5¢)
- [ ] 0.008 (0.8¢)
- [ ] 0.010 (1.0¢)
- [ ] 0.012 (1.2¢)
- [ ] 0.015 (1.5¢)
- [ ] 0.018 (1.8¢)
- [x] 0.020 (2.0¢) — **Baseline**
- [ ] 0.022 (2.2¢)
- [ ] 0.025 (2.5¢)
- [ ] 0.028 (2.8¢)
- [ ] 0.030 (3.0¢)
- [ ] 0.035 (3.5¢)
- [ ] 0.040 (4.0¢)
- [ ] 0.050 (5.0¢)
- [ ] 0.060 (6.0¢)
- [ ] 0.080 (8.0¢)
- [ ] 0.100 (10.0¢)
- [ ] 0.150 (15.0¢)
- [ ] 0.200 (20.0¢)
- [ ] 0.300 (30.0¢)
- [ ] 0.400 (40.0¢)
- [ ] 0.490 (49.0¢)

#### Queue Depth Filter (`queue_gate`)
- [ ] 0.0 shares (disabled — engine default)
- [ ] 5.0 shares
- [ ] 10.0 shares
- [ ] 20.0 shares
- [ ] 35.0 shares
- [x] 50.0 shares — **Baseline (CLI default)**
- [ ] 75.0 shares
- [ ] 100.0 shares
- [ ] 150.0 shares
- [ ] 200.0 shares
- [ ] 300.0 shares
- [ ] 500.0 shares
- [ ] 1000.0 shares
- [ ] 2500.0 shares
- [ ] 5000.0 shares
- [ ] 10000.0 shares
- [ ] 50000.0 shares
- [ ] 100000.0 shares

#### Order Share Size per Leg (`quote_shares`)
- [ ] 5 shares (engine default)
- [ ] 10 shares
- [ ] 20 shares
- [ ] 35 shares
- [ ] 50 shares
- [ ] 75 shares
- [ ] 100 shares
- [x] 120 shares — **Baseline (CLI default)**
- [ ] 150 shares
- [ ] 200 shares
- [ ] 250 shares
- [ ] 300 shares
- [ ] 500 shares
- [ ] 750 shares
- [ ] 1000 shares
- [ ] 2000 shares
- [ ] 5000 shares
- [ ] 10000 shares

#### Entry Delay in Seconds (`entry_delay_sec`)
- [x] 0.0 s (immediate quoting) — **Baseline**
- [ ] 1.0 s
- [ ] 2.0 s
- [ ] 3.0 s
- [ ] 5.0 s
- [ ] 10.0 s
- [ ] 15.0 s
- [ ] 20.0 s
- [ ] 30.0 s
- [ ] 45.0 s
- [ ] 60.0 s (1 min)
- [ ] 90.0 s
- [ ] 120.0 s (2 min)
- [ ] 180.0 s (3 min)
- [ ] 240.0 s (4 min)
- [ ] 300.0 s (5 min)
- [ ] 600.0 s (10 min)
- [ ] 900.0 s (15 min)
- [ ] 1800.0 s (30 min)
- [ ] 3600.0 s (60 min)

#### Relative Late Entry Delay (% of Window) (`entry_delay_pct`)
- [x] 0.0 % (disabled) — **Baseline**
- [ ] 0.01 % (1%)
- [ ] 0.02 % (2%)
- [ ] 0.03 % (3%)
- [ ] 0.05 % (5%)
- [ ] 0.08 % (8%)
- [ ] 0.10 % (10%)
- [ ] 0.12 % (12%)
- [ ] 0.15 % (15%)
- [ ] 0.20 % (20%)
- [ ] 0.25 % (25%)
- [ ] 0.30 % (30%)
- [ ] 0.40 % (40%)
- [ ] 0.50 % (50%)
- [ ] 0.75 % (75%)
- [ ] 1.00 % (100%)

#### Stop Loss Reversal Buffer (`exit_reversal`)
- [ ] 0.001 ($0.001)
- [ ] 0.005 ($0.005)
- [ ] 0.010 ($0.010)
- [ ] 0.012 ($0.012)
- [ ] 0.015 ($0.015)
- [ ] 0.018 ($0.018)
- [x] 0.020 ($0.020) — **Baseline**
- [ ] 0.022 ($0.022)
- [ ] 0.025 ($0.025)
- [ ] 0.028 ($0.028)
- [ ] 0.030 ($0.030)
- [ ] 0.035 ($0.035)
- [ ] 0.040 ($0.040)
- [ ] 0.050 ($0.050)
- [ ] 0.075 ($0.075)
- [ ] 0.100 ($0.100)
- [ ] 0.150 ($0.150)
- [ ] 0.200 ($0.200)
- [ ] 0.300 ($0.300)
- [ ] 0.500 ($0.500)

#### Leg Chase Enabled (`enable_leg_chase`)
- [x] False (disabled) — **Baseline**
- [ ] True (enabled)

---

### 2. Exit Thresholds per Slug / Duration (`exit_thresh_by_slug.*`)

Applied across all series (`default_5m`, `default_15m`, `btc-up-or-down-5m`, `sol-up-or-down-5m`, `btc-up-or-down-15m`, `sol-up-or-down-15m`):
- [ ] 0.01 ($0.01)
- [ ] 0.02 ($0.02)
- [ ] 0.03 ($0.03)
- [ ] 0.04 ($0.04)
- [ ] 0.045 ($0.045)
- [x] 0.050 ($0.050 / 5¢) — **Baseline**
- [ ] 0.055 ($0.055)
- [ ] 0.060 ($0.060)
- [ ] 0.070 ($0.070)
- [ ] 0.080 ($0.080)
- [ ] 0.090 ($0.090)
- [ ] 0.100 ($0.100)
- [ ] 0.120 ($0.120)
- [ ] 0.140 ($0.140)
- [ ] 0.160 ($0.160)
- [ ] 0.180 ($0.180)
- [ ] 0.200 ($0.200)
- [ ] 0.250 ($0.250)
- [ ] 0.300 ($0.300)
- [ ] 0.400 ($0.400)
- [ ] 0.500 ($0.500)

---

### 3. Structural Limits

#### Max Pair Cost Ceiling (`max_pair_cost`)
- [ ] 0.50 ($0.50)
- [ ] 0.60 ($0.60)
- [ ] 0.70 ($0.70)
- [ ] 0.80 ($0.80)
- [ ] 0.85 ($0.85)
- [ ] 0.90 ($0.90)
- [ ] 0.92 ($0.92)
- [ ] 0.94 ($0.94)
- [ ] 0.95 ($0.95)
- [ ] 0.96 ($0.96)
- [ ] 0.97 ($0.97)
- [ ] 0.98 ($0.98)
- [x] 0.99 ($0.99) — **Baseline**
- [ ] 1.00 ($1.00 - zero-loss ceiling)

#### Quotable Two-Sided Mid Range (`quote_range`)
- [ ] [0.00, 1.00] (unrestricted)
- [ ] [0.01, 0.99]
- [ ] [0.02, 0.98]
- [ ] [0.05, 0.95]
- [ ] [0.08, 0.92]
- [x] [0.10, 0.90] — **Baseline**
- [ ] [0.12, 0.88]
- [ ] [0.15, 0.85]
- [ ] [0.20, 0.80]
- [ ] [0.25, 0.75]
- [ ] [0.30, 0.70]
- [ ] [0.35, 0.65]
- [ ] [0.40, 0.60]
- [ ] [0.45, 0.55]

#### Dead Zone Value (`dead_zone_val`)
- [ ] 0.00 (disabled)
- [ ] 0.01 (1%)
- [ ] 0.02 (2%)
- [ ] 0.05 (5%)
- [ ] 0.08 (8%)
- [x] 0.10 (10%) — **Baseline**
- [ ] 0.12 (12%)
- [ ] 0.15 (15%)
- [ ] 0.20 (20%)
- [ ] 0.25 (25%)
- [ ] 0.30 (30%)
- [ ] 0.40 (40%)
- [ ] 0.50 (50%)
- [ ] 0.60 (60%)
- [ ] 0.75 (75%)
- [ ] 1.00 (100%)

#### Dead Zone Unit (`dead_zone_unit`)
- [x] pct (fraction of window) — **Baseline**
- [ ] sec (absolute seconds)

#### Unpaired Leg Policy at Expiry (`naked_leg_at_expiry`)
- [x] close (close at executable book bid) — **Baseline**
- [ ] hold (carry to window settlement)

---

### 4. Execution Assumptions (Held at Baseline during Primary OFAT)

#### Taker Fee Rate (`taker_fee_rate`)
- [ ] 0.00 (zero fee assumption)
- [ ] 0.01
- [ ] 0.02
- [ ] 0.03
- [ ] 0.05
- [x] 0.07 (crypto fee coefficient) — **Baseline**
- [ ] 0.08
- [ ] 0.10
- [ ] 0.12
- [ ] 0.15
- [ ] 0.20
- [ ] 0.30

#### Merging Gas Fee USD (`merge_gas_usd`)
- [x] 0.0 ($0.00 - sponsored gasless) — **Baseline**
- [ ] 0.001
- [ ] 0.005
- [ ] 0.010
- [ ] 0.020
- [ ] 0.050
- [ ] 0.100
- [ ] 0.200
- [ ] 0.500
- [ ] 1.000
- [ ] 2.000
- [ ] 5.000
- [ ] 10.000

#### Price Tick Size (`tick_size`)
- [ ] 0.0001
- [ ] 0.0005
- [x] 0.0010 ($0.001 / 0.1¢) — **Baseline**
- [ ] 0.0020
- [ ] 0.0050
- [ ] 0.0100
- [ ] 0.0200
- [ ] 0.0500

#### Minimum Order Size in Shares (`min_quote_shares`)
- [ ] 1 share
- [ ] 2 shares
- [x] 5 shares — **Baseline**
- [ ] 10 shares
- [ ] 20 shares
- [ ] 50 shares
- [ ] 100 shares
- [ ] 200 shares
- [ ] 500 shares
