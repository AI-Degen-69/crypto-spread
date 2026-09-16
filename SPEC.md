# SPEC — Issue #226: one fill rule, no `fill_model` knob

Binding while `fix/one-fill-rule-226` is live. Supersedes the #225 spec, which is
merged and closed. Per-issue working file (`docs/git-workflow.md` §5) — not an
architecture document.

The rule itself is already written and agreed: `docs/engine-decision-rules.md` §3
(`fill_rule`) and `docs/adr/0002-single-hard-coded-fill-rule.md`. Those two files
are the definition. This spec is the work that makes the code match them.

## Goal

A resting buy fills by exactly one rule in both engines, and the fill price says
which side of the trade we were on.

## The rule (restated, normative)

A resting buy at price `R` fills on a tick when **either**:

1. **Tape print** — a trade prints with `abs(price - R) <= tick_size + 1e-6`.
2. **Fully-crossed book** — `best_ask <= R - tick_size + 1e-6`. Strictly through,
   never a touch.

**Both are detectors of the same event, not two kinds of fill.** They answer "was
our order taken", not "what did we pay". The fill price is `R` and the fee is zero,
under either branch, with no same-tick precedence to decide.

| how we saw it | fill price | fee |
|---|---|---|
| tape print at our price | `R` | none |
| ask fully through our price | `R` | none |

**Why the second branch is not a taker fill.** An ask resting below our bid is not
a state a book can hold — they would have matched on contact. Seeing it in a
one-second snapshot is evidence our resting order was taken between snapshots, not
evidence we crossed into anything. The seller was the aggressor and the aggressor
pays. Operator correction, 2026-09-16, superseding an earlier draft (and the issue
body) that booked the ask price with a taker fee here.

**Entries never pay a fee; exits always do.** A stop or naked-leg timeout sells into
the best bid, which crosses the book. `_taker_fee` stays exactly where it is on
those paths and is not added to any entry path.

**The one marketable-limit case, knowingly under-charged.** The leg chase raises the
unfilled leg to `min(ask, max_affordable)`, which can land exactly on the ask; such
an order matches on arrival. It is booked as a maker fill at `R` with no fee, per
the operator's rule. Since `R == ask` in that case, only the fee is at stake. A
comment at the chase site records this.

## Acceptance criteria

1. `fill_model` does not exist: not on `BacktestParams`, not in the param registry,
   not in the `/api/backtest` query contract, not in the dashboard HTML, not as a
   CLI flag, not in any research simulator. `tapeq` is gone with it.
2. Every entry fill — either branch, either engine — books the resting price and
   adds nothing to `fees_cents`. No entry path calls `_taker_fee`.
3. The live paper simulation fills only on a fully-crossed book (`ask <= resting -
   tick`), at the resting price. Its WS tape fill is unchanged.
4. Exit paths are untouched: the stops, the naked timeout and the settlement mark
   still sell into the bid and still pay `_taker_fee`.
5. A parity test drives one shared snapshot sequence through both engines and
   asserts identical fill decisions **and** identical fill prices: tape-print case,
   fully-through case, no-fill-on-touch case.
6. The eight frozen sweep drivers — `research/sweeps/phase{1..6}*.py`,
   `validate_top.py`, `run_exit_rev_110.py` — are **deleted**, not patched.
   Operator decision, 2026-09-16: they cannot run against the current engine,
   nothing imports them, and keeping them alive only to carry a keyword we are
   deleting is the tail wagging the dog. Their `.json` result tables stay —
   `docs/ev-research-findings-2026-09-11.md` cites seven of them as the evidence
   `patient_band_maker` was chosen on.

## Out of scope

- Live (`mode="live"`) fill detection: real fills come from `get_order` /
  `associate_trades` and already carry the venue's own fill price. Untouched.
- Fee accounting in the live engine — it computes no fees at all today, tracked
  separately (`docs/engine-decision-rules.md` §2). Not this issue.
- Re-running or regenerating any historical sweep result.
- Queue-position modelling. `queue_gate` and the `_queue_ahead` telemetry stay as
  they are; only `tapeq`, which was a fill model, is removed.

## Known consequences (accepted)

- The live engine fills slightly less often than today (touch → fully-through).
- Backtest P&L moves because the *set* of fills changes — the shipped preset ran
  the tape branch alone and now runs both. Fill prices and entry fees do not move:
  an entry was booked at the resting price with no fee before this change, and
  still is.
- The chase-onto-the-ask case understates cost by one taker fee. Known, accepted.
- `BacktestParams.params_hash()` changes for every configuration, invalidating
  cached sweep artifacts keyed on it.
- `docs/ev-research-findings-2026-09-11.md` and `docs/backtest-optimization-results.md`
  describe a fill rule the code no longer has.
