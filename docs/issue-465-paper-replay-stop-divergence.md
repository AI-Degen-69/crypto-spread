# Issue #465 follow-up: paper-vs-replay stop divergence

**Question.** The 2026-10-06 re-gate night claimed `total_pnl = −95.69` while the strict replay of the
*same windows under the same recorded configuration* reconstructs `−35.92` — a 2.66× overstatement
of the loss, the **opposite sign** to #146's 100% optimism (`+6.74` claimed → `−19.50` at executable
marks). Why?

**Answer, in one line.** The two numbers describe **two different strategies**: the paper night ran
with a stop-loss that the replay does not model and that **real money would not have run either**.

## 1. Executive summary

1. The gap is **not** how the two engines price a completed pair. Over the 36 windows both engines
   traded, the edge per completed pair round is within half a cent of the same value — paper
   **5.50¢/share**, replay **6.01¢/share** — because both buy the pair at `1 − 2·offset` (~0.94).
2. The gap is the **exit ledger**. The paper booked **149 naked-leg exits** in scope; the replay booked
   **35**, and **zero** of them were adverse stops (`kinds == {settle: 20, dead_zone_close: 15}`).
3. Every one of the paper's **120** scoped "staged" stops prints `(drift X.XXX < 0.05)` in its own
   note: the mid-drift trigger **never fired**. The whole night contains **0** `Adverse drift …` notes
   across all 258 trades. The stops came from a branch that fires when the protected leg's **bid**
   touches `entry − 5¢`.
4. That branch is gated on `self.mode != "live"` (`strategy/live_trader.py:5024`, `:5059`) — it exists
   **only in paper mode**. The backtest engine has no such path at all (`grep -c stop_price
   backtest/engine.py` → `0`). So this stop can fire in paper, and can fire in neither of the two
   places a paper result is supposed to predict: real money, and the replay.
5. The knob meant to disarm the stop (`exit_reversal`, pinned at its maximum `0.50` by the pilot at
   `scripts/shadow_ev_pilot.py:58`) disarms **only the drift path**, by arithmetic, in *both* engines.
   It never reaches the paper-only bid-touch path — which is precisely why a night documented as
   "no stop-loss" took 144 stops.
6. Therefore the paper number is not evidence of paper pessimism about the strategy: it is mostly the
   cost of a stop that neither the replay nor a real-money run of this configuration would take. The
   gate verdict is unaffected — it was decided by the replay's own `replay_expectancy = −0.9978`
   (rule 2 of `docs/live-pilot-regate.md`) — but **neither number should be read as the strategy's
   expectancy until this is fixed** (§8).

## 2. The measurement

| | |
|---|---|
| Run | `runs/paper/2026-10-06_17-48_UTC+03-00/` — 2h, paper, universe xrp-15m + bnb-15m + eth-5m, 5 shares/leg |
| Recorded config | `offset 0.03`, `entry_delay_sec 60`, `exit_thresh 0.05`, **`exit_reversal 0.50`**, `max_pair_cost 0.98`, leg chase on, `naked_leg_at_expiry "close"` |
| Replay | `replay_comparison/replay_totals.json`, verdict leg `gates_pc1.0`, ticks `ticks_2026-10-06.jsonl` (verify `WARN`/`EXPLORATORY`) |
| Shared scope | the 36 windows in both the replay's strict scope and the paper's book (paper traded 41 markets; 5 predate tick coverage) |
| Per-window evidence | `replay_comparison/window_probe.json` — this investigation's per-window ledger (probe: `run/analysis/replay_stop_probe.py`, `run/analysis/stop_divergence_probe.py`, both gitignored scratch) |

In scope the paper's realized P&L is `−84.55` of the night's realized `−92.34` (total `−95.69`,
including `−3.35` unrealized); the 5 out-of-scope markets carry the rest.

## 3. Accounting — where the −48.63 difference lives

**Paper, 36 shared windows** (from `data/trades.jsonl`, categorised by its own notes):

| trigger family | n | pnl_usd |
|---|---:|---:|
| pair merge | 65 | **+17.86** |
| staged bid-touch stop | 120 | **−47.01** |
| dead-zone close | 26 | **−49.88** |
| spot fast stop | 3 | **−5.51** |
| **total** | **214** | **−84.55** |

**Replay, same 36 windows** (from the replay's own exit ledger):

| trigger family | n | pnl_usd |
|---|---:|---:|
| pair round | 96 | **+28.85** |
| naked-leg settle | 20 | **−43.04** |
| dead-zone close | 15 | **−21.73** |
| adverse stop | **0** | 0.00 |
| **total** | **131** | **−35.92** |

Decomposition of the `−48.63` in-scope gap (components rounded; the ledgers are read in cents):

- **−10.99** — the paper completed **31 fewer pair rounds** (65 vs 96). Each stop ends the round; the
  replay rides the same round to a chased pair instead.
- **−37.63** — the paper booked **114 more naked-leg exits** (149 vs 35). It pays less per episode
  (−0.69 vs −1.85) but pays 4.3× as often.
- **0.00** — marking and pair pricing are the same in both engines.

Whole-night paper counts (all 258 trades, 41 markets) are the same story: 144 staged bid-touch stops,
6 spot fast stops, 27 dead-zone closes, 81 pair merges, **0 drift-breach stops**.

## 4. Root cause 1 — a stop trigger that exists only in paper mode

The trading engine's stop has two independent triggers (`strategy/live_trader.py:5020-5036`):

```python
paper_stop_hit_up = (
    self.mode != "live" and mstate.stop_order_id and mstate.stop_side == "UP"
    and mstate.up_bid is not None and mstate.stop_price is not None
    and mstate.up_bid <= mstate.stop_price          # the BID touches the staged stop
)
drift_breach_up = (mstate.filled_up and not mstate.filled_down
                   and mstate.max_down_drift >= self.exit_thresh)   # the MID drifted 5c
if ((drift_breach_up or paper_stop_hit_up)
        and not mstate.reversal_seen_down and not mstate.exit_taken
        and mstate.status != "STOP_EXIT_PENDING"):
```

- The bid-touch branch is **paper-only** (`self.mode != "live"`). A paper leg is cut when its bid falls
  to the staged price; in real money nothing is submitted on that condition.
- The backtest engine has no order object and therefore no bid-touch path at all
  (`backtest/engine.py` contains no `stop_price`): its only adverse exit is the mid-drift trigger at
  `:1533` / `:1582`.
- Reading the two engines' ledgers side by side is therefore not a fidelity comparison; it compares
  *cut-and-re-enter* against *hold-and-chase*.

**Evidence that the bid-touch branch, not the drift branch, produced the paper's stops:** all 120
scoped stops (144 night-wide) carry notes of the form
`Staged stop hit: DOWN bid 0.36 <= stop 0.36 (drift 0.042 < 0.05)`; 83 of them report `max drift 0.000`,
i.e. the two-sided mid never moved adversely at all — the loss was realized purely because a **bid**
slipped 5¢ under our fill. There is not one `Adverse drift …` note in the night.

## 5. Root cause 2 — the disarm knob stops one path and not the other

The pilot sets `exit_reversal=0.50` (`scripts/shadow_ev_pilot.py:58`) with the comment "the exit_reversal
mercy rule stays wide"; the module docstring calls the configuration "no stop-loss". `0.50` is the
maximum the knob accepts (`strategy/live_trader.py:2958` clamps to `[0.001, 0.50]`).

Why the maximum disarms the drift path *by arithmetic*, in both engines:

1. Inside one tick, the drift tracking runs **before** the trigger block: both live in
   `_update_market_strategy` (def at `strategy/live_trader.py:4044`), drift at `:4390-4406`,
   trigger at `:5020` — confirmed by the enclosing-function check, not by reading order alone.
   The backtest mirrors the shape: it updates at `backtest/engine.py:1180-1194` and tests at `:1533`.
2. On the first tick where the excursion reaches `exit_thresh`, the trigger sees the **previous** max
   (still < 5¢) and does not fire; the same tick then raises the max *and* latches the reversal, because
   `excursion < exit_reversal` is true whenever `exit_reversal > exit_thresh` (5¢ < 50¢).
3. From the next tick on, `not reversal_seen` blocks the trigger **forever**. The stop is unreachable,
   not merely rare.

Measured, on the same 36 windows, by re-running the replay core with the buffer armed
(`run/analysis/stop_divergence_probe.py`):

| replay configuration | pair rounds | stops | dead-zone closes | settles | total |
|---|---:|---:|---:|---:|---:|
| `exit_reversal = 0.50` (as recorded) | 96 (+28.85) | **0** | 15 (−21.73) | 20 (−43.04) | **−35.92** |
| `exit_reversal = 0.02` (engine default, parity value) | 139 (+41.77) | **413 (−183.60)** | 2 (−0.10) | 0 | **−141.93** |

Two conclusions. (a) The "0 stops" in §3 is a property of the **latch**, not of the windows — the
trigger works fine at the default. (b) `exit_reversal` is a **first-order P&L knob** in the backtest
(±$106 between the two values on two hours), which is why it belongs in the run's own record —
recorded as candidate **N2** in `docs/issues/465-noticed-but-not-touching.md`.

The defect is therefore one knob with **two different reach**: wide enough to switch the drift trigger
off in both engines, but it never touches the trading engine's paper-only bid-touch trigger. A knob
that documents "no stop-loss" while a stop stays armed and fires 144 times is the same defect family as
Invariant 0 — the setting means something different from what it says.

## 6. Root cause 3 — the spot fast stop has no replay input

`m.spot_drift <= -self.spot_exit_drift` (`strategy/live_trader.py:1631`) fires a fast stop from the
RTDS/venue **spot** feed. The tick file carries no spot field at all (record keys: `cid`, `down_book`,
`duration`, `end_ts`, `err`, `iso`, `label`, `mid`, `queue_down`, `queue_up`, `resting_pair`, `series`,
`slug`, `start_ts`, `t_rem`, `tape_delta`, `touch_pair`, `ts`, `up_book`). Those exits — 6 night-wide,
3 in scope, −$5.51 — are **structurally impossible** in any replay built on this collector's snapshots.
This is a known and accepted modelling gap, recorded here so it is not mistaken for divergence noise.

## 7. What the replay would have done with the live trigger

Episode-level estimate (probe section B): for each of the replay's 35 naked-leg episodes, walk the
window's own snapshots across the naked interval and ask whether the leg's bid ever reached
`round(entry − 0.05, 2)` — the live paper condition.

- **35 of 35 episodes would have hit it. 0 would not.** Every naked leg the replay carried to the dead
  zone or to settlement had, at some earlier tick, been cuttable at the live engine's trigger.
- The replay booked those 35 episodes at **−$64.77**. Selling at the first triggering bid instead
  prices the same episodes at **−$19.85**.

This is an **episode-level estimate, not a re-simulation**: it ignores what the engine would do after
each cut (re-enter, chase, or lose again), so it is not a claim that "the paper trigger would have
made the night profitable". It says only that the divergence is *not* about marks that never existed —
the marks were there, and one engine acted on them while the other did not.

## 8. What this does and does not say

- **It does not reopen the gate.** `docs/live-pilot-regate.md` is append-only by its own constraint, and
  it decided **NO-GO** on the replay's `replay_expectancy = −0.9978 ≤ 0` (rule 2). Nothing here changes
  that number.
- **It does not read as paper pessimism about the strategy.** The paper night's extra `−$48.63` is
  almost entirely a stop the replay cannot take and real money would not take. Re-reading the `−95.69`
  headline as "the strategy does worse than the replay says" would be wrong.
- **It does not license the opposite reading either.** "The replay is the real-money truth" is a
  favourable claim resting on the same defect: a real-money run of this configuration has a stop armed
  in memory that can never fire (§5), which is its own open question, not a validated control.
- **What it establishes** is narrower and firmer: the two numbers in the register compare **two
  policies** (cut-and-re-enter vs hold-and-chase), not two fidelities of one policy. Any future gate
  must state which policy it is gating.

## 9. Reproduction

```powershell
python -m scripts.replay_shadow_check --run runs/paper/2026-10-06_17-48_UTC+03-00   # → -35.92
python run/analysis/replay_stop_probe.py        # per-window ledger → window_probe.json
python run/analysis/stop_divergence_probe.py    # counterfactual + episode estimate
```

The two probes live in gitignored `run/analysis/` and are evidence tooling, not deliverables. A fix
issue should promote the parts it needs into `scripts/` with tests.

## 10. Handoff

For the follow-up fix issue (not in #465's scope — no strategy or engine edit was made here):

1. **Decide the stop's real-money semantics first.** If the bid-touch trigger is the intended rule 2
   behaviour, it must exist in real money too (`docs/engine-decision-rules.md` §2 states the trigger as
   the *mid excursion*, so today's paper branch is arguably the undocumented one). If it is not
   intended, delete the paper branch.
2. **Make the disarm reach every path, or delete the dis-arm.** `exit_reversal` is a tuning knob;
   today it silently does not cover the paper bid-touch branch. A parity test at
   `exit_reversal >= exit_thresh` (asserting zero stops on both engines, or a documented exception)
   is the missing gate. Every existing stop test runs **below** the threshold (`exit_reversal` 0.02 or
   0.03 in `tests/test_stop_loss_parity.py`, 0.0 in `tests/test_backtest_engine.py:1089`, never ≥
   `exit_thresh` 0.05), so no test ever exercises the latched case that the pilot's 0.50 selects.
3. **Record the knobs the headline depends on.** `exit_reversal` is not in the manifest's
   `config_hypothesis` even though it moves this night by ±$106 (§5) — candidate N2 in
   `docs/issues/465-noticed-but-not-touching.md` records it without touching it here.
4. **Decide the spot-stop's status.** Either the collector captures a spot series and the replay
   models `spot_exit_drift`, or the paper/real-money runs stop using a trigger that no backtest can
   verify.

## 11. Limits

- One 2-hour night, one universe, 36 windows; the counts are large (413 stops in the armed
  counterfactual) but they are one evening's weather.
- The tick capture is `WARN`/`EXPLORATORY`, so per-tick marks are honest but incomplete; the argument
  here rests on the engines' own ledgers, not on any single mark.
- The episode estimate in §7 is a first-touch model (§7 caveat) and is not evidence about the
  strategy's expectancy either way.
