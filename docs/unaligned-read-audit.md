# Audit: unaligned reads compared as if simultaneous

**Class definition.** Two values that were read at *different instants* (or from different
sources at different cadences) are differenced or compared as though they described one instant.
No alignment is imposed, no age bound is applied, and the gap between the two reads is not
recorded — so the resulting "difference" conflates **staleness** with **disagreement**, and every
downstream number inherits the ambiguity.

The diagnosed instance is the WS-vs-REST book divergence (#438): `shadow_compare_book`
(`scripts/collect_ticks.py:556-631`) compares a freshly fetched REST book against the socket's
cached book with no timestamp on either side, and the resulting 16.8% turned out to be the age of
the socket's copy, not corruption in it. The residual verdict is in
`docs/issue-174-socket-book-disagreement.md` §7; the metric is being corrected under #440.

This audit looks for **other** instances of that class.

**Scope and method.** Read-only review of `strategy/`, `scripts/`, `backtest/`, `server/`
at `master` = `1e6246c`. Searched by name shape (`compare|divergen|reconcil|mismatch|drift|skew|age|stale|latch`)
and by the *two-source* shapes this repo actually has (WS book vs REST book, RTDS vs REST spot,
venue record vs local state, tape print vs book quote, recorded hash vs current file). No code was
executed; every claim below is a reading of the named lines.

---

## Findings

| # | Where | Severity | Acts on it? | Alignment today |
|---|---|---|---|---|
| 1 | `strategy/live_trader.py:4146-4180` — `_use_rest_for_leg` / `WS_BOOK_DRIFT_GUARD` | **High** | **Yes — REST overwrites the leg's book, live money** | Age bounded at 2.5 s, gap's ages never compared or recorded |
| 2 | `strategy/live_trader.py:5160-5210` — `_resolve_exit_bid` latches | **High** | **Yes — prices a real exit** | None: latches carry no timestamp |
| 3 | `strategy/streaming.py:1146-1164` — `reconcile_with_rest` | **Medium-High** | **Yes — order state** | Sorted by own timestamps, no watermark against the snapshot's |
| 4 | `scripts/replay_socket_reconciliation.py:356-359` + `:474-500` | Medium | Yes — the divergence rate | None for the fallback path; labelled `unknown`, still counted |
| 5 | `strategy/live_trader.py:1574-1583`, `:1612-1620` — `price_diff` | Low | No (telemetry only) | None, and the one timestamp covers only one of the two inputs |
| 6 | `backtest/engine.py:1363-1376` — tape print vs same tick's book | Low-Medium | **Yes — every replay fill** | Tick is timestamped; intra-tick order is not, bounded ≤1 s |

---

### 1. `WS_BOOK_DRIFT_GUARD` revokes socket authority on an unaligned comparison

**Evidence.** The guard is the body of the nested `_use_rest_for_leg(leg)` at
`strategy/live_trader.py:4146-4180` inside `_update_market_strategy` (`:4044`), returning `True`
when *REST may overwrite this leg's book*. Guard constant `WS_BOOK_DRIFT_GUARD_CENTS = 0.02` at
`:120`; socket age bound `ws_book_max_age_sec = 2.5` at `:1007`. The code compares
`mstate.up_bid/up_ask` (the maintained socket book, whose age is only "≤ 2.5 s") against
`rest_book` (`ubook`/`dbook`, fetched by the poll in the same pass) and, when either side differs
by more than 2¢, returns `True` — after which `mstate.up_bid/up_ask` are **replaced by the REST
values** for that leg (`:4183-4200`). So this is not a lost round of preference: a real, executable
quote is overwritten on the strength of an unaligned comparison, and the overwritten value is then
latched as `last_valid_*` (`:4198-4200`), feeding finding 2.

**Credited:** the decision is re-evaluated under `_book_reconcile_lock` immediately before the write
(`:4185-4191`) so a leg that became fresh between the check and the use is spared. That is the same
"value changed between the check and the use" hazard this audit is about, handled correctly — and
it is the right place to add the age comparison, since the lock already exists.

**Why it is this class.** The two books are read at different instants and the *ages of those two
reads are never compared, bounded relative to each other, or recorded*. #438 established that the
measured gap between a socket book and a REST read is a **monotone function of the REST reference's
age** and is essentially independent of which frame triggered the read (verdict §7.4). So a large
grown here has at least two explanations that the code cannot distinguish:

- the socket book is genuinely wrong (the guard's stated intent), or
- the socket book is merely **up to 2.5 s older** than the REST read it was just compared against
  (the artefact).

The docstring's own wording — *"a FRESH socket book still loses one round to REST"* — asserts the
second is excluded, but `fresh` here means only `age <= 2.5 s`, which is far wider than the ~100 ms
window below which #438 measured **zero** disagreement. This is the highest-value finding in the
audit: it is the only instance of the class that both runs in **live money** and **acts on the
difference**, and its failure direction is asymmetric — a false drift costs a REST round (quoting
downtime), while a genuinely stale-but-passing book keeps authority (the money-losing direction).

**Fix shape.** Compare ages, not just books: require the socket book's age to be small enough that
the pair is comparable (the #438 measurement gives ~100 ms as the bound below which the artefact
disappears), keep `<= ws_book_max_age_sec` as the outer servability bound rather than as the
comparability bound, and record *both* ages plus the reason whenever authority flips, so the flip
rate is auditable per run.

### 2. Exit prices resolved from latched quotes of unbounded age

**Evidence.** `_resolve_exit_bid` at `strategy/live_trader.py:5160-5210`. Stages 3 and 4 return
`last_valid_*_bid` / `1.0 - last_valid_*_ask` as the **executable exit price**; stage 5 falls back
to `mstate.mid` guarded only by `getattr(mstate, "last_update_ts", 0) > 0`. The latches are declared
as plain floats at `:689-692` (`last_valid_up_bid`, `last_valid_down_bid`, `last_valid_up_ask`,
`last_valid_down_ask`), written at `:2113-2128` (WS tape path) and `:4198-4219` (REST poll path),
and reset only at window rollover (`:5403-5406`).

**Why it is this class.** A latch is by construction a value read at *an earlier, unrecorded*
instant. Nothing bounds how much earlier: within a window the age can reach minutes, so a quote
observed near the open of a 15m window can price an exit at its close, and nothing downstream — not
the returned `(price, basis)` tuple, not the telemetry — can say how old it was. The stage-5 mid
guard checks that a book update was ever seen (`last_update_ts > 0`), not that it was seen recently;
that is a presence test standing in for a freshness test.

**Fix shape.** Timestamp each latch at write time and accept a latched price only within a bounded
age; when the bound fails, extend the returned basis string (the function already returns one:
`direct_bid`, `complement_ask`, `latched_bid`, …) with the age, so the exit record says
`latched_bid@8.4s` instead of `latched_bid`. The repo's own `ws_book_ts_*` +
`is_ws_book_fresh` (`:1918-1927`) is the pattern to copy.

### 3. `reconcile_with_rest` replays a buffer with no watermark

**Evidence.** `strategy/streaming.py:1146-1164`. The REST order snapshot is applied first, then
**every** buffered order event is replayed,sorted by its own timestamp (`:1160-1162`);
`start_buffering`/`handle_order_event` at `:1121-1129` collected them while the snapshot was
in flight.

**Why it is this class.** Sorting the events proves they are mutually ordered; it says nothing about
their order relative to the **snapshot**. Nothing compares an event's timestamp against the
snapshot's, so events *older* than the snapshot are applied on top of newer state: a stale
`LIVE`/open event can re-add an order the snapshot already reports as filled or cancelled, and a
stale cancel can clobber a newer re-open. The two reads (venue snapshot at `T`, buffered stream
events at `t ≤ T`) are reconciled without ever comparing `t` to `T`.

The repo already codified the rule for books — issue #171, "a REST poll must never overwrite
strictly newer socket data" (`strategy/book_math.py:247`, `strategy/live_trader.py:4238`) — but the
order-state path has no equivalent guarantee.

**Fix shape.** Capture a watermark with the snapshot (the max event timestamp observed, or the
snapshot's own time) and drop buffered events at or below it; or apply snapshot-then-newer-only.
`reconcile_with_rest` is the right place because it already owns "seed, then replay".

### 4. The corrected metric still counts references whose age it cannot state

**Evidence.** `scripts/replay_socket_reconciliation.py:356-359`: when a token has no REST snapshots,
`_select_rest_book` returns a legacy fallback book with **`rx=None`**. The caller at `:474-500`
counts the comparison, computes `age_s = None` (`:494`), buckets it as `AGE_UNKNOWN` /
`SKEW_UNKNOWN` (`:202-235`), and — crucially — **includes it in the `comparisons` and
`rest_divergences` totals that produce `divergence_rate`** (`:200`).

**Why it is this class.** It is the #438 defect surviving *inside the #438 fix*: a rate that
includes pairs whose reference age is unknown is still a rate over a mixed population. It is at
least visible now (the explicit `AGE_UNKNOWN` bucket is exactly the honesty the fix added), but
visible is not the same as excluded — the headline rate still carries them. On the #438 capture the
path presumably never fired (680 REST snapshots across 10 tokens), which means **the fix's own
blind spot is unexercised**.

**Fix shape.** Report the undated pairs as their own excluded count and compute `divergence_rate`
over the dated population only, the way #440 now requires the freshness-window exclusions
(103,094 pairs, 72%) to be reported rather than folded in. This belongs in #440's scope rather than
a new issue — it is the same instrument and the same fix.

### 5. `price_diff` differences two feeds with no per-field age

**Evidence.** `strategy/live_trader.py:1574-1583` (`on_rtds_tick` → `m.rtds_price = price`, then
`m.price_diff = round(m.actual_price - price, 4)`) and `:1612-1620` (`on_spot_tick` → the same
difference computed again). `actual_price` is set only for Binance-sourced spot ticks (`:1605-1608`),
`rtds_price` only from `on_rtds_tick`.

**Why it is this class.** Two spot reads from two feeds at two cadences (RTDS relay vs REST
fallback) are differenced with no per-field age, and the two are updated by different callbacks.
Compounding it, `on_rtds_tick` **never updates `spot_updated_ts`** — only `on_spot_tick` does
(`:1609`), against the field declared at `:815`. So an operator or a later reader of the market
state sees a difference plus a timestamp that describes only *one* of the two inputs, and will
reasonably read it as describing both.

**Why it is only Low.** Nothing decides on `price_diff` / `price_diff_pct`; they are cockpit
telemetry. The class is the same, the blast radius is display.

**Fix shape.** Either record both ages alongside the difference, or stop publishing a difference
whose inputs are not contemporaneous — the honest version of what #438's metric is being taught.

### 6. Every backtest fill compares a tape print against the same tick's book

**Evidence.** `backtest/engine.py:1363-1376`: `s["tape_delta"]` supplies the prints, and
`book_math.resting_bid_filled(resting_up, up_ask, up_prints, params.tick_size, …)` evaluates them
against `up_ask` read from the **same** snapshot `s`. The tick itself is timestamped (`:1092`,
invariant #224 — a snapshot with no timestamp is skipped, not invented), but nothing inside a tick
orders a print against a quote change.

**Why it is this class, and why it is structural.** The collector writes one book plus a
`tape_delta` per second, so within a tick a print at `t` and a quote change at `t+0.9 s` are
indistinguishable and are treated as simultaneous. The engine is honest about the consequences and
already documents the compensating band (`:260-281`): widening it "fabricates fills" — measured on
`ticks_2026-09-13`, `0.005` lifts P&L from `-579¢` to `-540¢`, which the comment correctly calls
invention rather than signal. The point for this audit is narrower: **part of what that band
absorbs is intra-tick ordering, not disagreement**, so it is the same instrument-artefact shape as
#438 at a 1-second floor. It is also why the paper-vs-replay divergence work (#465, #466) needs an
alignment story rather than a knob-recording fix alone.

**Fix shape.** Not a code fix at current data resolution. The durable fix is sub-second ordering in
the capture (the CLOB WS tape path already carries per-print timestamps — `on_ws_trade`,
`strategy/live_trader.py:1929-1946` — while the 1 s tick file flattens them). Record the finding
where replay fidelity is discussed, and stop reading a within-band fill as evidence of agreement.

---

## Reference implementations — the class done right

These exist in the repo already; they are the shapes the fixes above should copy.

| Pattern | Where | What it does right |
|---|---|---|
| Freshness-gated authority | `strategy/live_trader.py:1918-1927` (`is_ws_book_fresh`) | Records `ws_book_ts_*` at write time and gates **authority** on age, instead of comparing an undated cached book against a fresh read |
| Growing-prefix compare | `scripts/verify_tick_data.py:254-320` (`apply_hash_crosscheck`) | The same class for a growing file: compares the recorded **prefix**, not the whole file hashed at two instants; records state afterwards so the change flags once |
| Fingerprint-gated cache | `server/osc_dash.py:690-730` (`_read_verify_cache` + `_file_fingerprint`) | A cached verification is honoured only when its fingerprint matches the **current** file, so a stale cache cannot describe another generation |
| Age-aware reconciliation | `scripts/replay_socket_reconciliation.py:69-84`, `:202-235` | The #438 fix: ±0.5 s window, `note_skew`, and `note_age` recording the **denominator alongside the numerator** |

## Checked and cleared (not instances)

- `scripts/monitor_stream_latency.py` with `scripts/audit_all_markets.py` — comparing a sampled
  Binance ticker against a CLOB BBO *is* the measurement; the tool records its own local sampling
  offset (120–280 ms, `docs/rtds-clob-latency-audit.md` §2.2) and uses an explicit reaction window
  (10 s). The unaligned gap is the deliverable, not the defect.
- `strategy/live_trader.py:1626-1644` — the fast-stop `spot_drift` is computed from the tick being
  handled against `spot_open_price` captured at window open. A feed stall produces no tick and so no
  trigger; the baseline's age does not change what it means. Deliberate time-series drift.
- `scripts/collector_watchdog.py:250` (`manifest_age`), `scripts/ship_to_drive.py:57` (`is_stable`) —
  measuring age is the purpose.
- `strategy/markets.py` — no REST-vs-WS book comparison; the only reconciled pair is the #438 one.

## Limitations of this audit

- Searched by **name shape and known two-source pairs**, not by exhaustive data-flow analysis. A
  comparison built from two differently-named containers could have been missed.
- `server/osc_dash.py` (~14.6k lines) was spot-checked rather than read. One site deserves a second
  pass: `sync_wallet_trades` (`strategy/live_trader.py:3183+`) merges venue history into local
  records; a merge is not a difference, but whether it can overwrite newer local state the way
  finding 3 can deserves the same watermark question.
- No code was run; findings 1, 2, 4 and 6 are testable predictions (age distribution of guard flips;
  age of latched exit prices; the undated-pair count on a capture without snapshots; within-band
  fill share), and none has been measured yet.
