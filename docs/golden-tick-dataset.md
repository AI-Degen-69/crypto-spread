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
- A failing day is quarantined by moving its file **out of** `run/ticks/golden/` (to
  `run/ticks/quarantine/`) and recording the exclusion in the manifest — not by rewriting
  gigabytes. (The file itself must leave the directory: the set-level verify scans every
  `ticks_*.jsonl[.gz]` it finds there and does not read the manifest — see §2.3.)
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

- Every gate of §1.1 must pass. A failing day is quarantined: its file is **moved out of**
  `run/ticks/golden/` (to `run/ticks/quarantine/`) and the day + reason are recorded in the
  golden manifest. Removing a day from the manifest alone is not enough — `verify_tick_data`
  on a directory scans every `ticks_*.jsonl[.gz]` file present regardless of the manifest, so
  a file left behind would be silently included in certification step 2.
- A quarantined day does not abort the capture — the collector keeps running; the shortfall is
  covered by the headroom days (§1.2). If more than one day fails, extend the capture rather
  than lower a bar.

### 2.4 The freeze rule

**No collector-code changes while a golden capture is running.** The golden days must be
producible end-to-end by one collector build; a mid-capture collector change makes the
provenance in the manifest ambiguous (which days came from which code?). This is why issue
#174 (socket-authoritative books, which touches `scripts/collect_ticks.py`) waits until the
golden capture is complete and the set is certified.

## 3. Certification plan

The golden dataset is certified when this exact sequence passes. It is also the
re-certification sequence — every promotion of a new or replaced day re-runs it in full.

```powershell
# 1. Every golden day passes its per-day gate (§1.1)
#    <day-file> is the actual day filename — golden days may be .jsonl or .jsonl.gz (§1)
python -m scripts.verify_tick_data run/ticks/golden/ticks_<day>.jsonl[.gz]

# 2. The set as a whole meets the §1.2 bar
#    (only golden days are in the directory — quarantined files live in run/ticks/quarantine/)
python -m scripts.verify_tick_data run/ticks/golden

# 3. Fresh replay index for every golden day (index newer than its source file)
python -c "from pathlib import Path; from backtest.index import build_index, is_fresh; p=Path('run/ticks/golden/<day-file>'); build_index(p); assert is_fresh(p, p.with_suffix(p.suffix+'.idx'))"

# 4. Replay-speed budget holds (§4): read the printed total replay time T and the window
#    count W from the output; certification FAILS if T / W > 1.0 seconds per window.
python -m scripts.backtest run/ticks/golden --offset 0.02 --queue 50
```

### 3.1 The golden manifest

`run/ticks/golden/golden_manifest.json` records, per entry:

- `day` — the UTC day key of the source file.
- `verify_verdict` — the day's `status`, `capture_state().label`, and `readiness.level` at
  certification time.
- `sha256` — checksum of the day file, so a later tamper or partial rewrite is detectable.

Plus set-level totals (`windows_count`, `time_blocks`, `sampling_gap_rate`, ...), the
certification date, and the `READINESS_POLICY_VERSION` the certification ran under.

### 3.2 Promotion / re-certification policy

The golden dataset is **re-certified** (full sequence above, manifest rewritten) whenever:

1. A day is added or replaced — including a quarantined day's replacement.
2. `READINESS_POLICY_VERSION` changes — the old certification is void the moment the policy
   moves (`scripts/verify_tick_data.py:32`).
3. A verify or index tool change could alter a verdict or a sidecar — re-certify before the
   next backtest claim is made against the set.

A golden dataset whose manifest cites a policy version older than the installed one is **not**
the golden dataset — it is a stale copy, and the dashboard's readiness badges will show it.

## 4. Replay-speed budget

**Budget: a backtest or sweep over the golden dataset runs at ≤ ~1s per window wall-time on a
warm sidecar index.**

Grounding — three distinct operations, each labeled with what it actually measures:

| Operation | Cost | Evidence |
|---|---|---|
| **Measured**: full no-index **replay** of one day (467.07MB, 164,260 snaps, 550 windows) | 34.07s total ≈ ~62ms/window amortized | `docs/measurements/issue-221-gil-contention.json` |
| **Estimated**: per-call **scan** cost without index (what the slider pays per API call) | ~1.5s **per day** of data | `backtest/index.py:1-9` (docstring estimate) |
| **Measured**: indexed cid jump on a warm `.idx` sidecar | **~50ms** per call | `backtest/index.py:1-9` |

Implications, binding for certification:

- The ≤~1s/window budget governs **per-window reads** (the slider/API path, one call per
  window). Without a sidecar that call pays the scan estimate — ~1.5s/day × 5 days ≈ 7.5s,
  out of budget by construction. With a warm sidecar it is ~50ms, in budget with ~20× headroom.
- The 34.07s figure is a **batch** replay of one whole day, not a scan and not a per-window
  cost; it is the evidence that a full-day replay is affordable, and scaled linearly to five
  days ≈ 170s across ~2,750 windows ≈ ~62ms/window amortized. Certification step 4 measures
  exactly this operation on the golden set and enforces T/W ≤ 1.0s.
- A no-index scan of the golden set is out of budget **per call** by construction; the golden
  certification therefore requires a fresh `.idx` sidecar for every golden day
  (`backtest.index.is_fresh` — index mtime strictly newer than the source file), built by
  `backtest.index.build_index` (certification step 3).
- If a golden-day `.idx` is missing or stale, first replay rebuilds it automatically
  (`load_index`), but certification never relies on that lazy path — the sidecars are built and
  checked as part of the gate so the first research query is already fast.

