# Glossary

The agreed name for every entity in this system. One name per thing, and every name maps to
something you can point at in the code.

Agreed with the operator 2026-09-16 (issue #236). Where a name here disagrees with a name in
older code comments, this file wins and the comment is stale.

## The two engines

| Name | Code | What it does |
|---|---|---|
| **the trading engine** | `LiveTraderEngine`, [`strategy/live_trader.py`](../strategy/live_trader.py) | Decides and executes on markets that are open right now |
| **the backtest engine** | `_simulate_window` / `replay`, [`backtest/engine.py`](../backtest/engine.py) | Runs the same rules over windows that have already closed |

They read the same rules and hold **independent parameter values** — that is the design, not a
compromise ([ADR-0001](adr/0001-parity-harness-over-shared-module.md)).

## Execution mode

`LiveTraderEngine.mode`, shown in the UI as **Execution Mode**. Two values:

| Name | Value | What changes |
|---|---|---|
| **paper** | `"paper"` | Orders are recorded locally; fills are simulated from the tape and the book |
| **real money** | `"live"` | Orders go to the Polymarket CLOB; fills are read back from the venue |

**The trading engine runs in both.** It decides identically in both — there is no `if self.mode`
anywhere in the decision path. The mode switches only how an order reaches the market and how a
fill is learned: placement, fill discovery, the stop handle (`STAGED` vs `RESTING`),
cancellation, and which balance is shown.

This is why "live" is not a name for the engine, and why the mode's own name is **real money**.

## The five tabs

| Name | `switchTab()` | Label on screen |
|---|---|---|
| **the trading platform** | `cockpit` | Trading Platform |
| **the market data tab** | `marketdata` | Collector's Market Data |
| **the backtest tab** | `backtest` | Backtest Sweeper |
| **the summary tab** | `summary` | Stats Summary |
| **the files tab** | `ticks` | Tick Files |

The trading platform is the trading engine's **display**, not the engine. The engine keeps
running with the tab closed.

The market data tab shows the collector's books and queue depth. It decides nothing.

*(The `cockpit` element-id prefix is left alone: it is internal, and unambiguous.)*

## "Live" is retired

No label in the dashboard says "Live" any more. The stream-health pill reads
`● STREAM · <1s` / `● OK · 1s` / `● OFFLINE · POLLING`, and the execution-mode pill reads
`REAL MONEY` / `PAPER TRADING`.

The only place the string survives is `LiveTraderEngine`, `mode="live"` and the `/api/live/*`
routes — code identifiers and an API contract, not names we speak. When referring to them out
loud, use **the trading engine** and **real money**.

## Data

| Name | What it is |
|---|---|
| **the collector** | [`scripts/collect_ticks.py`](../scripts/collect_ticks.py) — polls the venue and writes snapshots |
| **a tick file** | `run/ticks/ticks_YYYY-MM-DD.jsonl` — one snapshot per line |
| **a snapshot** (= a tick) | One line: both books, the tape delta, the window metadata at one instant |
| **a window** | One market from `start_ts` to `end_ts`. Always the market, never a panel in the UI |
| **the golden dataset** | The single canonical backtest dataset: verified golden days + the golden manifest in `run/ticks/golden/`, per [`golden-tick-dataset.md`](golden-tick-dataset.md) |
| **a golden day** | One UTC day file inside the golden dataset that passed its per-day verify gate (`PASS` + `COMPLETE CAPTURE`) |
| **the golden manifest** | `run/ticks/golden/golden_manifest.json` — which days are in the set, each day's verify verdict, and the policy version the certification ran under |

The collector misses seconds. That gap is why the fill rule reads the tape *and* the book
([ADR-0002](adr/0002-single-hard-coded-fill-rule.md)), and it is why a backtest predicts
behaviour rather than reproducing it tick for tick.

## Terms that have already caused bugs

**mid** means the two-sided mid, `book_math.two_sided_mid` — both legs, both sides. If either
leg cannot be priced it is `None`, and `None` is an answer, not a problem to be substituted away.

**the recorded mid** is the `"mid"` field in a tick file. It is `book_math.mid(up_book)` — the
**up leg alone** — so it survives a book that priced only one side. It is never called "mid",
and it is never an anchor. Reading it as one was issue #225.

**a tuning knob** is swept and tuned freely: offset, stop loss, entry delay, share size.
**a structural limit** bounds what the engine may do at all: `max_pair_cost`, the quote range,
the dead zone. Both are changeable; only the first is part of the tuning set
([ADR-0003](adr/0003-structural-limits-separate-from-tuning-knobs.md)). "Parameter" on its own
means a tuning knob.

**a run** is one backtest over one parameter set. **a sweep** is many runs,
[`scripts/sweep_backtest.py`](../scripts/sweep_backtest.py).

**a rule** is an entry in [`engine-decision-rules.md`](engine-decision-rules.md): a name, what it
does, its trigger, and the action on trigger. **an invariant** sits above every rule — there are
two, and they are in the same file.

## Where the names live

- **What the strategy does** — [`engine-decision-rules.md`](engine-decision-rules.md)
- **Why the system is shaped this way** — [`adr/`](adr/)
- **What each thing is called** — this file
