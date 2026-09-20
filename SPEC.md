# SPEC — Issue #285: Railway trial + Google Drive as the file store

## Goal
Run the golden-capture collector on the operator's Railway trial ($5 credit,
30 days) with the operator's Google Drive (4TB free) as the file store, because
trial volumes cap at 500MB while 5 days need ~8GB raw.

## Deliverable 1 — Railway service definition
- `nixpacks.toml`: Python + `rclone` via apt (no new pip dependency —
  `requirements.txt` stays at 5), start command runs the watchdog with
  `COLLECT_OUT` at the 500MB volume mount and `COLLECT_EXTRA_ARGS=--gzip`
  (raw days are ~1.5–1.7GB; gz days are the only thing that fits the buffer).
- Trial math (recorded): ~$1.50 compute for 6 days sits inside the $5 credit;
  2 vCPU / 0.5GB RAM per service is plenty for the collector loop.

## Deliverable 2 — Drive shipper
- New `scripts/ship_to_drive.py`: uploads only CLOSED days (a day file is
  closed when `now_day_key()` has moved past it — the live day is still being
  appended by `write_snap`, `scripts/collect_ticks.py:303-315`, and must never
  be shipped mid-write), invoked from the watchdog loop behind a
  `DRIVE_REMOTE`-set flag (default off — Windows behavior unchanged).
- Transport is `rclone` as a subprocess with env config
  (`RCLONE_CONFIG_GDRIVE_*`, token pasted by the operator — service accounts
  cannot see personal-Drive storage, so OAuth refresh token in env, never in
  code). `google-api-python-client` is explicitly NOT used (new dep + same
  OAuth problem, zero gain).
- Per shipped day: the `.jsonl.gz`, a matching `.sha256` sidecar (charter §3.1
  needs one checksum per golden day), plus a manifest snapshot for provenance;
  a local `shipped.json` state file so restarts never re-upload; retry with
  backoff, failures logged not raised (a stuck shipper must never kill capture).

## Deliverable 3 — Runbook extension
- `docs/collector-hosting-runbook.md` gains the Railway+Drive path: service
  setup, one-time Drive auth (operator, on their PC), logs, pull-from-Drive +
  checksum check. Render/Fly sections stay as-is.

## Out of scope (explicit)
- Collector capture logic or verify thresholds (frozen mindset; #281 owns capture).
- `verify_tick_data` changes — pulled files verify locally, unchanged.
- Re-deciding #283's Render pick — Railway-trial is the operator's override for
  this run, documented as such.
