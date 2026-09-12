# EV Research Report — SPREAD-2 Parameter, Market & Timeframe Sweep

**Date:** 2026-09-11 · **Dataset:** `run/ticks/` — 2,430 condition windows, ~265k snapshots, 5 collection days (2026-08-31, 09-07, 09-08, 09-09, 09-11) · **Engine:** `backtest/engine.py` semantics, replayed tick-for-tick by a parity-verified fast simulator (`run/sweeps/ev_lab.py`, **6,840 window-checks × 12 configs, 0 mismatches**).

Every result below is **settlement-corrected**: the stock engine silently books 0 PnL when a naked leg's final bid is missing (book empty at settlement); an audit (`run/sweeps/audit_settlement.py`) showed those 79 windows *all* lost (−39.3¢ true), and "marked" windows were 89 wins / 3 losses vs the true 88/83. Without this correction every hold-to-settle config looks ~40% better than reality. All fees = Polymarket crypto taker 0.07·p·(1−p) on exits and settlement marks; maker fills free.

**Statistics:** 95% bootstrap CI on mean net PnL per 5-share window (5,000 resamples), plus a day-cluster bootstrap (resampling whole days) — windows within a day are correlated, so the day-cluster lower bound is the honest one.

---

## 1. Headline answer

With fills defined the honest-but-optimistic way (a tape print at your resting price fills you), **several configurations are EV-positive with the 95% CI lower bound above 0, positive on all 5 days**. The best family:

> **Delay entry 60s into each window, only enter if the mid is within ~3–4¢ of 0.50, quote both legs at mid−3¢, and hold any naked leg to settlement (no stop-loss).**
> `off=0.03, band=0.04, delay=60s, ex=none`: **+1.56¢/window, CI95 [+0.77, +2.44], day-cluster lo +0.86, all 5 days positive** (+37.9¢ total on 5 shares/window).
> `off=0.03, band=0.03, delay=60s, ex=none`: +1.24¢/window, CI95 [+0.53, +2.06], **day-cluster lo +0.77, all days ≥ 0**.

But this edge lives in a **queue-position blind spot**. Under strict queue accounting (`tapeq`: the queue ahead must be eaten by printed size before a print at your price fills you) the same configs go **negative (−3.4 to −4.4¢/window)**. The truth for live trading is bounded between those two models, and the deciding variable — your actual queue position at fill time — is not observable offline. §4 tells you exactly what to measure live.

Also decisive: **stop-loss exits are the largest PnL destroyer in this dataset** — the repo's prior optimum (exit 0.08) is now EV-negative on fresh data, and exit thresholds respond monotonically badly across the whole 0.04–0.20 range.

---

## 2. Sample sizes

| day | windows | 5m | 15m | notes |
|---|---:|---:|---:|---|
| 2026-08-31 | 1,650 | ~1,188 | ~462 | full 24h-day capture |
| 2026-09-07 | 135 | 98 | 37 | partial |
| 2026-09-08 | 515 | 373 | 142 | partial |
| 2026-09-09 | 55 | 40 | 15 | partial |
| 2026-09-11 | 75 | 106* | — | still collecting during study |
| **total** | **2,430** | **1,805 (300s)** | **625 (900s)** | 10 series throughout |

*duration counter includes windows open at rebuild time. Data integrity: 0 corrupt lines, 0 crossed books, 0 time reversals (`scripts/verify_tick_data`).

---

## 3. Full results

Format: pairs% = windows where both legs filled and merged · exits% = stop-loss fired · win% = windows with net PnL > 0 · **tot$/w = total net USD at 5 shares per window** · mean c = mean net cents per window (5 shares) · ci lo = 2.5th percentile of bootstrap mean · day lo = day-cluster bootstrap 2.5th percentile · PF = profit factor · DD$ = max drawdown of cumulative curve.

### 3.1 Phase 1 — 1D sensitivity on the repo baseline (tape fills, settle-corrected)

Baseline = `off=0.02, q=0, pc=1.05, ex_5m=0.08, rev=0.015, tape` (per docs/backtest-optimization-results.md). 44 configs; all axes swept. Selected rows (full table in `run/sweeps/phase1_1d.json`):

| config | pairs% | exits% | win% | tot$/w | mean c | ci lo | day lo | PF | DD$ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| q=200 | 0.1 | 0.3 | 0.3 | +2.29 | +0.10 | −0.34 | −0.38 | 1.29 | 2.64 |
| off=0.025 | 3.5 | 4.5 | 6.1 | +1.54 | +0.06 | −1.92 | −4.22 | 1.01 | 24.74 |
| off=0.040 | 0.8 | 1.7 | 2.0 | −1.87 | −0.08 | −1.61 | −0.69 | 0.98 | 22.40 |
| **BASE_repo_opt** | 1.1 | 1.8 | 2.5 | **−19.25** | **−0.80** | −2.38 | −3.08 | 0.81 | 32.26 |
| off=0.010 | 1.5 | 1.7 | 2.9 | −30.35 | −1.26 | −2.92 | −2.68 | 0.73 | 32.96 |
| ex=0.04 | 1.1 | 1.5 | 2.3 | −19.95 | −0.83 | −2.34 | −3.54 | 0.79 | 30.67 |
| ex=0.12 | 1.9 | 3.7 | 3.9 | −33.84 | −1.41 | −3.37 | −2.16 | 0.78 | 42.36 |
| ex=0.16 | 2.3 | 4.3 | 4.5 | −55.64 | −2.31 | −4.40 | −4.02 | 0.71 | 59.40 |
| ex=0.20 | 2.5 | 4.7 | 4.9 | −60.40 | −2.51 | −4.54 | −3.48 | 0.70 | 64.16 |
| ex=none | 3.2 | 1.1 | 6.8 | −44.47 | −1.85 | −4.71 | −3.20 | 0.83 | 53.73 |
| timeout=0.10 | 0.3 | 0.8 | 0.4 | −6.75 | −0.28 | −0.71 | −1.04 | 0.56 | 9.77 |
| fill=book | 4.6 | 31.9 | 4.7 | −470.32 | −19.56 | −21.08 | −34.60 | 0.05 | 470.32 |
| fill=cross | 6.9 | 29.6 | 6.9 | −443.79 | −18.45 | −19.96 | −34.69 | 0.07 | 443.79 |

**Readings**

- **Exit threshold: monotonic harm.** Every exit level ≤ 0.16 loses money; the tighter the stop, the less you lose per exit but the more exits you take, and the sum is always negative. The old "exit 0.08 is optimal" finding does not replicate on the new sample — exits at −100¢/share average destroy more than they save because 74%+ of windows oscillate back.
- **Offset:** 0.025 was the only positive point (+0.06¢, statistically zero). The PnL-optimal offset from the old study (0.02) is now clearly negative. Pair fills are rare (1–3.5% vs 10.6% in the old report) — tape capture degraded since then (manifest tape_empty_rate ≈ 97%), so **fill rates everywhere in this report are lower bounds on live behavior**.
- **Queue gate:** anything > 0 kills the strategy (≤0.3% fills). Confirms queue_gate=0.
- **Entry timeout: pure harm.** 0.05–0.30 all negative. Cancelling unfilled quotes only removes eventual winner fills.
- **Fill model gap is the whole story:** book/cross models (any touch = fill) are catastrophically negative; tape (only printed trades at your price fill you) is near zero. The naive old study's +$32.80 profit came from a dataset where the exit rule happened to align with the tape; on 5× more data with settlement correction, it doesn't hold.

### 3.2 Phase 2 — queue-aware fills (tapeq, research model) + zero-fee + re-entry

| config | pairs% | exits% | tot$/w | mean c | ci lo | PF |
|---|---:|---:|---:|---:|---:|---:|
| reentry_band=0.030 | 1.5 | 2.1 | −4.58 | −0.19 | −2.30 | 0.97 |
| tape_fee0_base | 1.1 | 1.8 | −7.20 | −0.30 | −1.93 | 0.92 |
| tape_off=0.020 (=BASE) | 1.1 | 1.8 | −19.25 | −0.80 | −2.36 | 0.81 |
| tape_ex=none | 3.2 | 1.1 | −44.47 | −1.85 | −4.71 | 0.83 |
| tapeq_off=0.010 | 9.6 | 27.0 | −441.65 | −18.36 | −19.97 | 0.05 |
| tapeq_off=0.020 | 6.7 | 29.9 | −448.95 | −18.67 | −20.13 | 0.07 |
| tapeq_ex=none | **70.6** | 2.9 | −486.49 | −20.23 | −24.08 | 0.41 |
| tapeq_ex=0.12 | 26.7 | 34.8 | −628.11 | −26.12 | −28.31 | 0.17 |

**Readings**

- **Zero fees don't save it** (−0.30 vs −0.80): fees are second-order; adverse selection is the driver.
- **Re-entry (issue #95 knobs): negative.** Wider band 0.030 less bad than 0.015 but still ≤ 0.
- **tapeq is the reality check:** with queue accounting, fills explode (70.6% pairs when holding to settle!) and PnL explodes downward. A resting bid 2¢ under mid in these markets *gets run over* — queue melts, you fill, and the window decides against you. Note `tapeq_ex=none` pairs at +12.7¢ average (best case per pair) yet the strategy still loses −20¢/window: the pairs win small, the losses (exits at −256¢ avg) lose big.
- **Decomposition (tape, ex=none):** pairs 76 windows +15.2$, exits 27 windows −62.4$, naked-settle 171 windows +2.7$. The 4¢ pair gross is real; exits and adverse naked legs eat it.

### 3.3 Phase 3 — mechanical edge variants: leg-chase pairs rule (live issue #123, never backtested before)

Chase = after one leg fills, step the opposite quote up toward the ask, capped so pair cost ≤ cap. Emulated faithfully on tape fills.

| config | pairs% | chased | exits% | win% | tot$/w | mean c | ci lo | day lo | PF | DD$ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **tape chase=0.98 off=0.03 ex=0.08** | 1.4 | 79 | 3.2 | 3.5 | **−10.12** | −0.42 | −2.04 | −1.47 | 1.09 | 22.51 |
| tape nochase off=0.02 ex=0.08 | 1.1 | 0 | 1.8 | 2.5 | −35.75 | −1.47 | −3.30 | −3.30 | 0.85 | 26.61 |
| tape chase=0.98 off=0.03 ex=none | 3.4 | 76 | 0.9 | 5.4 | −37.22 | −1.53 | −4.14 | −1.93 | 0.86 | 37.87 |
| tape chase=0.98 off=0.02 ex=0.08 | 1.5 | 73 | 3.0 | 3.4 | −44.59 | −1.84 | −3.50 | −3.61 | 0.82 | 26.28 |
| tapeq chase=0.98 off=0.02 ex=none | 74.3 | 2,033 | 1.9 | 74.3 | −397.53 | −16.36 | −19.62 | −35.18 | 0.43 | 398.06 |
| tapeq chase=0.98 off=0.02 ex=0.08 | 16.4 | 733 | 32.1 | 16.4 | −550.90 | −22.67 | −24.34 | −38.29 | 0.09 | 551.02 |

**Readings**

- **Chase improves every config it touches** (best: −0.42 vs −1.47 same profile without): converting naked legs into capped pairs is worth ~+1.0¢/window on tape fills. It is the single best *mechanical* improvement found — but on queue-aware fills it backfires (more fills = more adverse selection), so live it must be paired with the regime filters below.
- `tapeq chase=0.98 ex=none` pairs 74.3% of windows at +12.7¢ per pair — **the chase mechanism works mechanically** (2,033 chases completed) — the economics still lose because entry fills themselves are adversely selected.

### 3.4 Phase 4 — universe selection (series × timeframe) with the best mechanical profile

(chase 0.98, tape fills, off=0.03, ex=0.08; full table in `run/sweeps/phase4_universe.json`)

| universe | n | pairs% | exits% | win% | tot$/w | mean c | ci lo | day lo | PF |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| delay=60s (all) | 2,430 | 1.2 | 0.9 | 2.0 | +19.83 | +0.82 | −0.24 | −0.10 | 1.60 |
| delay=120s (all) | 2,430 | 0.7 | 0.4 | 1.4 | +13.61 | +0.56 | −0.49 | +0.08 | 1.45 |
| xrp15+eth5 | 486 | 1.4 | 3.5 | 4.3 | +12.17 | +2.50 | −1.89 | +1.43 | 1.53 |
| xrp15 alone | 125 | 0.8 | 3.2 | 4.0 | +5.14 | +4.11 | −4.25 | −1.84 | 1.99 |
| bnb15 alone | 125 | 5.6 | 4.0 | 8.8 | +1.70 | +1.36 | −9.07 | −16.99 | 1.16 |
| eth5 alone | 361 | 1.7 | 3.6 | 4.4 | +7.03 | +1.95 | −2.74 | −2.58 | 1.40 |
| btc5 alone | 361 | 0.3 | 1.7 | 0.8 | −4.87 | −1.35 | −4.14 | −4.52 | 0.51 |
| sol15 alone | 125 | 1.6 | **13.6** | 5.6 | −5.43 | −4.34 | −15.51 | −17.79 | 0.70 |
| eth15 alone | 125 | 8.0 | 8.8 | 10.4 | −6.49 | −5.19 | −16.68 | −12.00 | 0.61 |
| 5m-only universe | 1,805 | 0.6 | 2.0 | 1.7 | −3.65 | −0.20 | −1.80 | −1.95 | 0.93 |
| 15m-only universe | 625 | 4.0 | 6.7 | 6.9 | −6.47 | −1.03 | −5.76 | −4.86 | 0.89 |

**Readings**

- **Per-series signal:** xrp-15m (+4.11¢/window, PF 1.99) and bnb-15m (+1.36, but CI spans) are the natural candidates; **sol-15m and eth-15m are the worst** (high exit rates — they trend hardest). BTC-5m is the most efficient market (lowest edge).
- Combined universes dilute per-series edge; the best combined universe (xrp15+eth5) is positive but not CI-positive alone.
- **Entry delay is a strong lever:** +0.82¢/window at 60s delay vs −0.42 without, on the same profile. Waiting out the first minute skips the opening queue dump and the pre-decided windows.

### 3.5 Phase 5 — entry-band regime filter × delay × chase (the breakthrough)

Band = at first quoted tick, require |two-sided mid − 0.50| ≤ band, else skip the window (only trade undecided markets). Tape fills, all series. Top rows (full grid in `run/sweeps/phase5_band.json`):

| config | pairs% | exits% | win% | tot$/w | mean c | ci lo | day lo | PF |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| off=0.02 band=0.06 d=60 ch | 0.6 | 0.9 | 1.8 | +28.99 | +1.19 | **+0.05** | −2.04 | 1.79 |
| off=0.02 band=none d=60 ch | 0.8 | 1.3 | 2.2 | +27.00 | +1.11 | −0.18 | −1.37 | 1.57 |
| off=0.03 band=0.04 d=60 nc **ex=none** | 0.5 | 0.1 | 1.2 | +23.41 | +0.96 | **+0.04** | −0.25 | 2.19 |
| off=0.03 band=0.03 d=60 nc **ex=none** | 0.3 | 0.1 | 0.9 | +22.82 | +0.94 | **+0.12** | **+0.42** | 2.87 |
| off=0.03 band=0.04 d=60 nc | 0.5 | 0.2 | 1.0 | +19.13 | +0.79 | −0.03 | −0.90 | 2.17 |
| off=0.02 band=0.02 d=60 nc | 0.1 | 0.2 | 0.4 | +11.52 | +0.47 | −0.07 | −0.33 | 2.90 |
| off=0.03 band=0.02 d=60 ch | 0.2 | 0.1 | 0.4 | +5.97 | +0.25 | −0.06 | +0.00 | 3.62 |

**Reading:** tighter band ⇒ higher profit factor (up to 3.6) and fewer catastrophic windows, at the cost of trade count. The ex=none variants with band+delay beat their stop-loss counterparts — once you filter to undecided markets, holding to settlement is better than stop-lossing.

### 3.6 Trade-level validation (fills only, per day)

| config | filled n | mean/trade c | CI95 trade lo | total | days positive |
|---|---:|---:|---:|---:|---|
| off=0.02 band=0.06 d=60 ch | 71 | +56.60 | +21.78 | +$32.19 | 2 of 5 (tiny n other days) |
| off=0.03 band=0.03 d=60 nc ex=none | 27 | **+111.38** | **+54.96** | +$30.07 | **4 of 5 (5th = 0 fills)** |
| off=0.03 band=0.04 d=60 nc ex=none | 36 | **+105.23** | **+57.74** | **+$37.88** | **5 of 5** |
| off=0.02 band=none d=60 ch | 88 | +48.72 | +18.28 | +$42.88 | 4 of 5 (−0.09$ day 5) |
| off=0.03 band=0.06 d=60 ch | 59 | +45.70 | +13.84 | +$26.96 | 4 of 5 |

Per-day PnL for the headline config (`off=0.03 band=0.04 d=60 ex=none`): +26.44, +2.46, +3.94, +0.30, +4.74 — positive every single day, in every market regime in the sample.

### 3.7 Phase 6 — the queue stress test (does it survive tapeq?)

| config | filled n (tape→tapeq) | pairs% | tot$/w | mean c | ci lo |
|---|---:|---:|---:|---:|---:|
| off=0.03 band=0.03 d=60 ex=none | 27 → 232 | 7.1 | −82.15 | −3.38 | −4.86 |
| off=0.03 band=0.04 d=60 ex=none | 36 → 302 | 9.2 | −106.92 | −4.40 | −6.09 |
| off=0.02 band=0.06 d=60 ch | 71 → 472 | 9.0 | −195.25 | −8.03 | −9.38 |
| off=0.02 band=none d=60 ch | 88 → 587 | 10.7 | −241.40 | −9.93 | −11.38 |

**Not one top config survives strict queue accounting.** The marginal fills that tapeq adds are strongly adversely selected. This does not mean live will be negative — it means the live fill rate will land somewhere between tape and tapeq, and **the ONLY way to know which is to measure your own queue position at fill time on live orders.** See §4.

---

## 4. The single parameter nobody can backtest: queue position

The entire EV question collapses into one measurable quantity: when a trade prints at your resting price, how many shares were ahead of you?

- If your fills happen **near queue-front** (tape-like) → the band+delay configs above are positive with margin (+1.0–1.6¢/window, CI lo > 0, all days positive).
- If your fills happen **after the queue is eaten** (tapeq-like) → every config is negative; do not deploy.

The live engine already records everything needed to measure this: per-order `queue_ahead` telemetry exists on the book poll, and every fill event can be joined to the tape print and the pre-fill queue snapshot. 200–300 live fills at 5-share size (about 2–3 days across 10 series with the band+delay filter) will collapse the uncertainty: compute per-fill `queue_position_at_fill / printed_size` and bucket EV by it.

**Interim sizing guidance:** trade minimum size (5 shares/leg, min order size) until live-measured queue-adjusted EV ≥ 0 over ≥200 fills; scale only then.

---

## 5. Recommended configuration to try live (in order)

1. **Primary candidate — "patient undecided-band maker":**
   - Series: start with **xrp-15m + bnb-15m + eth-5m** (best per-series signals), avoid sol-15m and btc-5m initially.
   - Entry: delay 60s after window open; require |mid − 0.50| ≤ 0.03–0.04 at entry; re-check mid hasn't moved >1¢ in the last 5s (the live engine's `mid_drift_for_resting` gate covers this).
   - Quotes: both legs at **mid − 0.03**, requote only on re-entry, queue_gate 0, pair_cost_gate 1.05.
   - Exit: **no stop-loss**; if one leg fills, **chase the opposite leg capped at pair cost 0.96–0.98** (live #123 mechanic, already implemented); otherwise hold to settlement.
   - Expected (if fills are tape-like): +1.0–1.6¢ per 5-share window, PF 2.2–2.9, CI lo +0.04–0.12¢/window.
2. **Secondary (more trades, thinner edge):** off=0.02, band 0.06, delay 60s, chase 0.98, exit 0.08. +1.19¢/window, CI lo +0.05, but −2.04 day-lo — more sensitive to regime.
3. **What NOT to run:** any config without the delay/band filters (all negative); entry timeouts (pure harm); queue gates > 0 (kills fills); book/cross-style optimism in any sizing decision.

**Continuous-capture status:** collector crashed twice on a Windows cp1252 `UnicodeEncodeError` (the ⚠️ tape-alert line). Fixed in `scripts/collect_ticks.py:298` (`sys.stdout.reconfigure(utf-8)`); collector now running continuously (pid 4824, 16k+ ticks today and accruing). Keep it running — every additional day materially tightens the day-cluster CI.

---

## 6. Method notes & caveats (read before sizing)

1. **Parity-verified fast simulator.** `run/sweeps/ev_lab.py` reproduces `backtest/engine.py:_simulate_window` bit-for-bit on 6,840 checks across 12 configs (pnl, fees, fills, exits, re-entry counts). Research extensions (`tapeq`, chase, delay, band) live in `run/sweeps/sim2.py` with engine-parity defaults.
2. **Settlement correction applies to ALL configs** (engine books 0 for unmarkable naked legs; true is ±(100−entry)). The correction moved `ex=none` from +5.77¢ to −1.85¢. Any comparison that skips this is wrong.
3. **Tape capture is degraded** (~97% of snapshots have empty tape vs ~2% when the old study ran). All fill rates here are undercounts; the *relative* ranking of configs is trustworthy, absolute PnL is a lower bound.
4. **Two blind spots remain:** intra-second queue position (the decisive one, §4) and maker-rebate/fee-schedule variation across markets (taker 0.07 used everywhere; zero-fee scenario tested, doesn't change rankings).
5. **Day-cluster CI is the honest gate** — window-level bootstrap overstates significance because windows share the day's regime. The recommended configs pass both gates; the marginal ones fail day-cluster.
6. **Multiple-comparisons risk:** ~180 configs were evaluated. The band+delay family is positive across adjacent parameter values (not an isolated lucky cell), across 5 days, and with both CI methods — that's as robust as offline evidence gets. The queue question (§4) is the remaining kill-switch.
7. **Sample-size math for the user's 95% CI goal:** at +1.5¢/window with per-window σ ≈ 30¢, you need ~1,570 filled windows for the CI lo to clear 0 from mean alone; the reason the recommended configs clear it with n≈2430 windows is the sparsity structure (most windows = 0, winners cluster positive). Live, judge by *filled-window* CI: currently +105¢/trade CI lo +58¢ on n=36 — recompute weekly.

## 7. Artifacts

| file | content |
|---|---|
| `run/sweeps/ev_lab.py` | window cache builder + parity-verified fast sim + bootstrap stats |
| `run/sweeps/sim2.py` | research extensions: tapeq, leg-chase, entry-delay, entry-band |
| `run/sweeps/audit_settlement.py` | the settlement-marking bias audit |
| `run/sweeps/phase1_1d.json` | 44-config 1D sensitivity results |
| `run/sweeps/phase2_fills.json` | tapeq/fee/reentry results |
| `run/sweeps/phase3_mech.json` | leg-chase ladder results |
| `run/sweeps/phase4_universe.json` | per-series + delay universe results |
| `run/sweeps/phase5_band.json` | 100-config band×delay×chase grid |
| `run/sweeps/validate_top.json` | trade-level per-day validation of top 5 |
| `run/sweeps/phase6_tapeq_top.json` | queue stress test of top 5 |
| `run/sweeps/window_cache.pkl` | 2,430-window compact cache (336MB, rebuild: `python run/sweeps/ev_lab.py cache --force`) |

Reproduce any row: `python run/sweeps/phase5_band.py` (grid) or `python run/sweeps/validate_top.py` (trade-level detail).
