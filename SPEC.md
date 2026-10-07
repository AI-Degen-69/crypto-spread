# SPEC.md — Issue #438: Root-cause the residual WS-vs-REST book divergence

Branch: `i438/root-cause-ws-vs-rest-book-divergence` | Issue: #438

## 1. Objective & Scope

Name the mechanism that leaves part of the WS-vs-REST best-quote comparisons divergent
after the #362 ghost-level fix, using a capture that actually contains REST ground truth,
and leave behind a minimal fixture that reproduces it plus a test that pins it.

This is a **diagnosis**. It ships instrumentation and a finding. It does not ship a fix,
a threshold change, or any production behaviour change.

**In scope**
- Make the recorder's REST path impossible to fail silently, and assert a REST-bearing
  capture at session end.
- Produce one fresh REST-bearing capture (post-#362) — the input the diagnosis needs.
- Extend the offline replay: per-series aggregation, magnitude buckets, and a
  REST-vs-WS timing delta per divergence so the sampling-skew hypothesis can be tested.
- Write the verdict into `docs/issue-174-socket-book-disagreement.md` as a new section.
- A minimal fixture under `tests/fixtures/` reproducing the residual mechanism through
  `CLOBMarketWSClient`, plus a targeted test asserting it.

**Out of scope (explicit)**
- Any change to `BOOK_SHADOW_TOLERANCE`, the GO threshold, or the GO/NO-GO badge semantics.
- Switching quoting or execution to the socket book.
- Hardening `CLOBMarketWSClient` (reconnect invalidation, staleness gating, crossed-book
  detection) — that is #440, and it stays blocked until this names a cause.
- The collector's per-series REST cost.

## 2. Acceptance Criteria

1. A named cause for the dominant divergence population, cited by file:line or by capture
   evidence that isolates it.
2. A minimal fixture committed under `tests/fixtures/` that reproduces the failure through
   `CLOBMarketWSClient`, plus a targeted test asserting it.
3. The bimodal split reported **per series**: rate and magnitude buckets, so the defect
   population is separated from the skew population.
4. Hypothesis 4 (benign sampling skew) explicitly **ruled in or out with evidence**.
5. `docs/issue-174-socket-book-disagreement.md` gains the verdict; the Phase 1 verdict text
   is **not** silently rewritten.
6. No production behaviour change.

## 3. Facts established at planning (verified, not assumed)

These were checked against the live codebase in Station II and they shape the plan:

- **The residual has never been measured against REST.** The only session on disk,
  `run/diag_ws/raw_session_2026-09-30_04-57-51.jsonl`, holds **35,333 lines, all `type: "ws"`,
  zero `type: "rest"`** — measured this session.
- **The capture predates the code that records REST.** The session file was written
  `2026-09-30 07:58:18 +0300`; the recorder's REST path landed in commit `b3a44cf`
  at `2026-09-30 08:35:10 +0300` — **37 minutes later**. So the REST path has never once
  produced a record, and the "0 REST snapshots" is an artefact of chronology, not of a
  broken path that was observed failing.
- **Both failure modes are silent.** In `scripts/record_raw_socket_session.py` the REST loop
  writes only `if b:` — a falsy/empty book is skipped with **no log at all** — and an
  exception is swallowed as `log.warning("REST full_book failed for %s: %s", ...)`.
- **The replay's REST pillar is implemented but never exercised.**
  `scripts/replay_socket_reconciliation.py:162` parses `rest` records, `:170-178` stores them,
  `:180-190` selects a temporally matched one, `:295-317` computes per-event-type REST
  divergences. Nothing has ever fed it a REST line.
- **The replay deliberately undercounts relative to the collector.** `_select_rest_book`
  matches within **±0.5 s** (`abs(best.rx - ws_rx) <= 0.5`) and returns `None` — skipping the
  comparison entirely — when the REST candidate disagrees with the in-frame declared quotes.
  That behaviour is intentional and already pinned by
  `tests/test_replay_socket_reconciliation.py:264`.
- **The 16.8% headline comes from a different instrument with no temporal alignment.**
  `scripts/collect_ticks.py:556` `shadow_compare_book` compares the freshly-fetched REST book
  against the socket's cached book at that instant (`:948-952`), with no timestamp comparison
  and no skew guard. **The replay's REST rate must therefore not be expected to reproduce
  16.8%** — the two measure different populations.
- **The magic-number tick collapses one bucket.** `TOLERANCE = 0.001`
  (`replay_socket_reconciliation.py:40`) equals `tick_size = 0.001`
  (`backtest/engine.py:283`), and divergence requires *strictly greater* than tolerance — so
  the issue's "`<= 1 tick`" bucket is empty by construction.
- **No test file exists for the recorder** (`tests/test_record_raw_socket_session*.py` absent),
  so the REST-recording path has zero coverage.

## 4. Edge Cases

- **A capture with no REST records is not a usable input.** If the fresh session reports
  `rest_snapshot_count == 0`, stop and fix the capture — do not analyse it.
- **Empty/falsy book vs failed fetch.** Today both collapse to "no record". They are different
  causes and the hardened path must distinguish them.
- **Skew vs corruption.** A comparison where the REST read is later than the WS mutation that
  set the top quote is sampling skew, not a defect. It must be separable in the output.
- **Sub-tolerance differences.** Anything below one tick is invisible to the current metric;
  the plan must say so rather than imply the population was measured.
- **A thin session.** Too few comparisons ⇒ no cause can be named. Report the count and
  extend the capture rather than concluding from noise.
- **Historical artefacts.** The frozen #174 Phase 1 numbers are prior art and are never
  rewritten.

## 5. Key Rules

- **Diagnosis only.** Any fix this uncovers becomes its own issue; #440 is the hardening
  successor and stays blocked by this one.
- **The instrument is not the answer.** Every number reported must name which instrument
  produced it (collector shadow path vs offline replay) and over which population.
- **No silent capture.** A REST side that yields nothing must be loud at session end.
- **Historical reproduction preserved.** The existing `--fixture-out` / fixture replay path
  keeps working; additions are additive.
- **Honest verdicts.** "Hypothesis 4 is the cause" is a valid, publishable result — and if it
  wins, the conclusion is that the metric, not the socket, needs the change.
