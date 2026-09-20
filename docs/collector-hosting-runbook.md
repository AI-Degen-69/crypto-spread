# Collector Hosting Runbook (Issue #283)

> Only the tick collector moves off-machine. Dashboard and trading engine stay local.
> Host decision: **Render** (background worker + disk). **Fly.io** is the cheaper
> fallback. Full comparison: `../SPEC.md` Deliverable 1.

## 1. Deploy (Render, primary)

1. Dashboard → New → **Background Worker**, repo `crypto-spread`, branch with this
   change (watchdog POSIX path, #283).
2. Build: `pip install -r requirements.txt`. Start command:
   `python -m scripts.collector_watchdog` (it spawns and guards `collect_ticks`).
3. Add a **persistent disk**: size **10GB**, mount path `/opt/render/project/src/run/ticks`.
   Only files under the mount survive restarts — the collector defaults to
   `run/ticks/`, which lands on the disk when mounted at that path. Prefer this
   over `COLLECT_OUT`: the watchdog and collector logs (`run/watchdog.log`,
   `run/collector.log`) live under `run/` *outside* the mount and are
   ephemeral — after a restart, read them fresh from the Logs tab, not the disk.
4. Environment (optional, wired in `collector_cmd`):
   - `COLLECT_OUT=/data` — only if the disk is mounted somewhere else.
   - `COLLECT_EXTRA_ARGS=--gzip` — only if disk is tight (`.jsonl.gz` verifies clean).
   No secrets needed (public market data). Confirm disk shows ≥ 10GB free
   before the proof run.

## 2. Check logs

> Never run a manual `python -m scripts.collect_ticks --once` on the host while
> the worker is up: its command line trips the watchdog's duplicate-collector
> path and one of the two processes gets killed. Stop the worker first, or
> proof from the downloaded files (§4) instead.

- Render dashboard → service → Logs. Healthy signs:
  - `watchdog up (check=60s stale=180s)` once at boot.
  - `STARTED collector pid=...` once; **no** `WEDGED` / `DUPLICATES` lines.
- Proof gate (≥ 1 hour): fetch the manifest from the disk and check
  `sampling_interval_s` ≈ 1.4s warm. Command (Shell tab on the service —
  honours `COLLECT_OUT` when the disk is mounted elsewhere):
  `python -c "import json,os;print(json.load(open(os.path.join(os.environ.get('COLLECT_OUT','run/ticks'),'manifest.json')))['sampling_interval_s'])"`

## 3. Restart

- Render restarts the worker automatically on crash. Manual restart: dashboard →
  Manual Deploy → Deploy latest commit (disk contents survive).
- Wedged collector (manifest stale > 3 min): the watchdog kills + restarts it by
  itself and logs `WEDGED: ... killing [...]`. No operator action needed; if
  `WEDGED` repeats, redeploy and inspect `run/collector.log`.

## 4. Pull day files (with checksums)

From the service Shell tab (or `scp`/`render disks` equivalent), per closed UTC day:

```bash
sha256sum run/ticks/ticks_<YYYY-MM-DD>.jsonl* > ticks_<YYYY-MM-DD>.sha256
```

Download both the day file and its `.sha256`, then locally (charter cite:
`docs/golden-tick-dataset.md` §3.1 needs one checksum per golden day):

```powershell
python -m scripts.verify_tick_data run/ticks/ticks_<YYYY-MM-DD>.jsonl
```

Gate: no structural errors. The recorded `sha256` goes straight into the golden
manifest later (charter §3.1 needs one checksum per golden day).

`--gzip` check (if disk is tight): set `COLLECT_EXTRA_ARGS=--gzip` on the
service, confirm a `.jsonl.gz` day file verifies clean.

## 5. Fly.io fallback (cheaper, ~$4–5/mo)

Needs a `Dockerfile` (python:3.12-slim, `pip install -r requirements.txt`,
`CMD ["python", "-m", "scripts.collector_watchdog"]`) — not yet in repo:

```toml
# fly.toml sketch
[processes]
app = 'python -m scripts.collector_watchdog'
[[restart]]
policy = "always"
[[mounts]]
source = "tickdata"
destination = "/data"   # + COLLECT_OUT=/data (same redirect convention as Render §1)
```

```bash
fly volumes create tickdata --size 10 --region iad
fly deploy
fly logs   # same healthy signs as §2
```

## 6. Operator-blocked checklist (needs the real host)

- [ ] TASK-3: `--once` passes ON the host.
- [ ] TASK-4: 1+ hour proof (manifest excerpt + watchdog log pasted into #283).
- [ ] TASK-5: day file pulled, `verify_tick_data` clean, `sha256` recorded.

## 7. Railway trial + Drive path (issue #285 — operator's override)

Trial math: ~$1.50 compute for 6 days sits inside the $5 credit; the 500MB
volume holds 2–3 gz days as buffer while the shipper mails finished days up.

1. **Service:** New Python service from this repo/branch. `nixpacks.toml` at
   the root installs `rclone` and starts the watchdog. Add a **500MB volume**
   mounted at `/data`.
2. **Variables** (dashboard, never in code):
   `COLLECT_OUT=/data`, `COLLECT_EXTRA_ARGS=--gzip`,
   `DRIVE_REMOTE=gdrive:crypto-ticks`, plus `RCLONE_CONFIG_GDRIVE_TYPE=drive`
   and `RCLONE_CONFIG_GDRIVE_TOKEN` from step 3.
3. **One-time Drive auth (on your PC, once):** install rclone, run
   `rclone authorize "drive"` (or `rclone config` → new remote → Drive →
   paste the browser token), then copy the resulting token JSON into
   `RCLONE_CONFIG_GDRIVE_TOKEN`. Service accounts cannot see personal-Drive
   storage — this personal token is the way in.
4. **Logs:** same healthy signs as §2, plus `ship: {'shipped': [...], ...}`
   lines once a day closes. A failed upload is logged and retried — capture
   never stops for shipping.
5. **Pull a day:** from Drive (web or `rclone copyto`), then locally:
   `sha256sum -c ticks_<day>.jsonl.gz.sha256` and
   `python -m scripts.verify_tick_data run/ticks/ticks_<day>.jsonl.gz`.
