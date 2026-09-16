# SPEC — Issue #228: `quote_range` replaces `entry_band` and `adverse_open`

Binding while `fix/quote-range-228` is live. Supersedes the #227 spec, which is
merged and closed. Per-issue working file (`docs/git-workflow.md` §5) — not an
architecture document.

The rule itself is already written and agreed: `docs/engine-decision-rules.md` §6
(`quote_range`). That file is the definition. This spec is the work that makes the
code match it.

## Goal

One quotable range, evaluated every tick, latching nothing: inside it, quote;
outside it, do not. The two `|mid - 0.50|` gates and their permanent latches go,
together with the re-entry workaround that existed only to undo one of them.

## Acceptance criteria

1. **Both gates gone from both engines.** `entry_band` (field, validation,
   registry entry, band block, `band_hold`, `band_gate_evaluated`, `band_skip`
   flags/stats/status) and `adverse_open` (open snapshot, `open_drift`,
   `open_gate_evaluated`, `adverse_skipped`, `entry_cancelled` sets that belong
   to these two gates) are deleted. `entry_cancelled` sets owned by the entry
   timeout and the late-start skip stay — #229/#230 own those.
2. **`quote_range` exists in both**, default `(0.10, 0.90)`, validated
   (`0.0 <= lo < hi <= 1.0`, else `ValueError`; live clamps through
   `update_config` like its other knobs), classified as a structural limit in
   the registry (no per-surface override).
3. **Evaluated every tick on the two-sided mid, never latched.** A mid outside
   the range holds placement on that tick only; already-resting quotes are
   unaffected (the order is on the venue); already-filled legs are unaffected
   (the chase and the exits are not entry). No `band_hold`-style pre-evaluation
   hold: before the first two-sided tick there is no mid to judge, which is the
   existing `no_book_hold`, not a new gate.
4. **Leave-and-return is quotable.** A market that exits the range and re-enters
   is quoted again in the same window (parity test, both engines).
5. **The drift-skip re-entry mechanism is deleted in both engines** (backtest
   `adverse_skipped` block; live `_maybe_reenter_drift_skipped` + re-entry
   telemetry + `reentry_mid/drift` state). It resurrected adverse-skipped
   windows only, and with no latch there is nothing to resurrect. `reentry_*`
   knobs with no remaining readers (`reentry_drift_band`,
   `max_reentries_per_window`, `min_requote_remaining_sec`,
   `reentry_min_remaining_pct`) go with it; any knob #229 still needs is
   reintroduced there, not kept warm here.
6. **The patient preset is deleted** (`PATIENT_BAND_MAKER`, its table entry, its
   config plumbing, its tests). Operator decision 2026-09-16: the preset's
   identity was the undecided-band maker, and the band is gone. No replacement
   preset in this issue.
7. **Every surface follows.** CLI `--entry-band` becomes `--quote-lo/--quote-hi`
   (defaults 0.10/0.90); dashboard Backtest + Cockpit band inputs become
   lo/hi inputs with `min="0" max="1"` (plain fields, no toggle — the #227
   pattern); `/api/backtest` `entry_band` query key becomes `quote_lo/quote_hi`;
   `sim2`'s `entry_band` argument becomes `quote_range` (same per-tick,
   never-latched semantics); `ev_lab.UNSUPPORTED_KNOBS` and `selection_bias`
   band configs follow the rename. `replay_shadow_check` gates legs keep
   `entry_delay` as the gates axis; the `entry_band` leg of the mirror is
   deleted.
8. **Closes #213** (entry_band's own issue). First half of #208; the issue body
   names it. #212 (re-entry resurrects adverse windows without re-pricing) is
   mooted by criterion 5 — the PR body proposes closing it, operator confirms
   at merge.

## Out of scope

- The dead zone (#229), the stop threshold (#230), the entry timeout /
  late-start skip, the chase, the exits. Untouched.
- The full registry structural-vs-tuning split (#233): this issue classifies
  only `quote_range` as structural; the rest waits for #233.
- New presets. None in this issue.

## Edge cases

- Mid exactly on a boundary (0.10 / 0.90): inside — the range is inclusive.
  Pin with a test.
- No two-sided mid on a tick: no judgement possible — placement holds via the
  existing `no_book_hold`, resting quotes stand. Not a range rejection.
- `quote_range` narrower than the resting spread (e.g. mid 0.89, offset 0.03
  rests the cheap leg at 0.08): fine — the range judges the market, not the
  order prices (rule text: "Measured on the mid").
- `max_reentries_per_window` / `reentry_*` readers: verified zero at build
  time; if a reader outside the re-entry path surfaces, it is kept and named
  in the commit body rather than deleted.
