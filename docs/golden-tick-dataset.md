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

## 2. Collection plan

### 2.1 The run

One uninterrupted, watchdog-guarded capture of **5 consecutive full UTC days**:

```powershell
python -m scripts.collector_watchdog   # detached; restarts a dead or wedged collector
python -m scripts.collect_ticks        # the collector the watchdog keeps alive
```

- `scripts/collector_watchdog.py` restarts the collector when the process dies and kills +
  restarts it when the manifest goes stale (wedged, ~10s cadence per `run/ticks/manifest.json`).
  Every event is logged to `run/watchdog.log`.
- Day-boundary rotation is the collector's own: `write_snap` appends to
  `run/ticks/ticks_<day_key>.jsonl` and `now_day_key` flips at UTC midnight — no operator action
  and no file stitching at boundaries.

### 2.2 Expected volume (measured, not assumed)

- A full UTC day costs **~1.5–1.7GB raw, ~160k snapshots** (measured on this machine:
  `ticks_2026-09-14` 1.55GB, `ticks_2026-09-15` 1.70GB; the GIL baseline counted 164,260 snaps
  in 467MB of `ticks_2026-09-13` — `docs/measurements/issue-221-gil-contention.json`).
- Cadence is round + `POLL_INTERVAL`, real gap ~1.4s — read `sampling_interval_s` from the
  manifest, not the 1s the old docs claimed (#167, `docs/operations.md`).
- 5 days ≈ **8–9GB raw** and ~800k snapshots. Disk space must be checked before starting;
  `--gzip` halves-plus the footprint if needed (`.jsonl.gz` is first-class in verify and index).

### 2.3 Per-day acceptance gate

After each day closes (UTC midnight), verify it before the next one is trusted:

```powershell
python -m scripts.verify_tick_data run/ticks/ticks_<day>.jsonl
```

- Every gate of §1.1 must pass. A failing day is quarantined: recorded in the golden manifest
  with the reason and the verify verdict, and excluded from the set.
- A quarantined day does not abort the capture — the collector keeps running; the shortfall is
  covered by the headroom days (§1.2). If more than one day fails, extend the capture rather
  than lower a bar.

### 2.4 The freeze rule

**No collector-code changes while a golden capture is running.** The golden days must be
producible end-to-end by one collector build; a mid-capture collector change makes the
provenance in the manifest ambiguous (which days came from which code?). This is why issue
#174 (socket-authoritative books, which touches `scripts/collect_ticks.py`) waits until the
golden capture is complete and the set is certified.

its own — it requires the existing gates to pass with the headroom stated in §1.2.
