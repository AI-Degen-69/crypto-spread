# The Golden Tick Dataset — Charter

> The single canonical tick dataset for **all** backtesting and sweeps. One definition,
> one location, one certification gate. Chartered in issue #280; executed in issue #281.
> Every number below is expressed in terms of metrics `scripts/verify_tick_data.py` already
> emits — nothing here invents a new measurement.

## 1. Definition

**The golden dataset** is the one set of tick data every backtest, sweep, and readiness claim
is run against. It lives at:

```
run/ticks/golden/
```

- **Golden days** — the verified per-day tick files (`ticks_YYYY-MM-DD.jsonl[.gz]`) that make
  up the set. One file per UTC day, exactly as the collector rotates them
  (`now_day_key`, `scripts/collect_ticks.py:369`).
- **The golden manifest** — `run/ticks/golden/golden_manifest.json`: every source day with its
  verify verdict, the totals, and the `READINESS_POLICY_VERSION` the certification ran under
  (`scripts/verify_tick_data.py:32`).

The golden dataset is **not** one physically concatenated file. Verified day files are kept
as-is and the manifest declares which days are in the set. This is deliberate:

- `backtest/index.py:group_by_cid_indexed` (`backtest/index.py:117-124`) already accepts a
  directory and merges per-file `.idx` sidecars in ts order — replay over the set needs no
  concat step and no new tooling.
- A failing day is quarantined by removing it from the manifest, not by rewriting gigabytes.
- Per-day verify verdicts stay per-day; a concat would bury a bad day inside a good file.
- Issue #281 explicitly leaves this either/or to the charter: "either concatenates verified
  days into one canonical file or records the canonical file list in a manifest".

### 1.1 Quality bar — per day

Every golden day must pass:

| Gate | Required value | Source metric |
|---|---|---|
| Integrity | `status == "PASS"` | verify report top-level status |
| Capture state | `capture_state().label == "COMPLETE CAPTURE"` | `scripts/verify_tick_data.py:254-257` |
| Corrupt rows | `corrupt_rate == 0` | `assess_readiness` check (`scripts/verify_tick_data.py:310`) |
| Collector errors | `collector_error_rate == 0` | `assess_readiness` check (`scripts/verify_tick_data.py:313`) |
| Time reversals | `0` | `verify_window_continuity` (`scripts/verify_tick_data.py:374-383`) |

A day that fails any gate is **excluded** from the golden set and recorded in the manifest with
the reason. Failed days are never silently mixed in, and passing days are never re-captured.

### 1.2 Quality bar — the set as a whole

The set must reach `readiness.level == "RESEARCH_READY"` **with headroom** above the policy
floors (`READINESS_POLICIES`, `scripts/verify_tick_data.py:34-59`):

| Metric | Golden target | Policy floor (RESEARCH_READY) |
|---|---|---|
| Series coverage | all 10 series from `strategy/series.py:SERIES` | `min_market_duration_pairs == 10` |
| Time blocks (days) | `time_blocks >= 5` | `min_time_blocks == 3` |
| Windows | `windows_count >= 500` | `min_windows == 100` |
| Windows per market | `min_windows_per_market >= 50` | `min_windows_per_market == 10` |
| Sampling gap rate | `sampling_gap_rate <= 0.05` | `max_gap_rate == 0.10` |
| Valid ticks | `>= 50,000` | `min_valid_ticks == 10_000` |

Why 5 days and not the floor of 3: three full days is the minimum that satisfies the policy;
five gives a spare day when one fails its per-day gate without restarting the capture, and
keeps the set above `min_time_blocks` even after a quarantine.

### 1.3 Continuity budgets — per window

Within every captured window (`verify_window_continuity`, `scripts/verify_tick_data.py:336-341`):

- **Late start** `<= 5s` after window open (`max_start_delay` — the verify default).
- **Early cutoff** `<= 5s` before window close (same threshold, same default).
- **Zero time reversals.** The 2026-09-15 double-writer incident produced 2,799 of them in one
  day (`scripts/collector_watchdog.py:57-61`); reversals mean two writers touched the file and
  the day is unusable, not merely warnable.
- Sampling gaps (`> max_gap_sec = 6s`) count against `sampling_gap_rate` in §1.2.

These are the existing verify thresholds, not new ones. The golden bar adds no stricter gate of
its own — it requires the existing gates to pass with the headroom stated in §1.2.
