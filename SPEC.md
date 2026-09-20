# SPEC — Issue #283: Host the tick collector on a managed platform for the 5-day golden capture

## Goal
Move ONLY the tick collector off the home PC onto an always-on managed host with
persistent disk, so the 5-day golden capture (#281) runs with no sleep/reboot/
internet-drop risk. Everything else (dashboard, trading engine) stays local.

## Deliverable 1 — Host decision (documented, 2026-09-20)
- Vercel is ruled out (serverless sleep + ephemeral disk). Hardening the home
  PC was rejected. Charter volume baseline: 5 days ≈ 8–9GB raw, ~800k
  snapshots (`docs/golden-tick-dataset.md` §2.2) → need >= 10GB persistent disk.

| Host | Fit for this workload | Cost for 1 month always-on + 10GB disk | Verdict |
|---|---|---|---|
| **Render** (background worker + persistent disk) | Native Python worker, no Dockerfile; disk mount is a form field; single instance (required for disk); platform restarts crashed services; disk persists across restarts (`render.com/docs/disks`, `/background-workers`) | Starter worker $7 + 10GB × $0.25 = **~$9.50/mo** (`render.com/pricing`) | ✅ **Recommended** — least moving parts |
| Fly.io (Machine + volume) | `restart: always` policy explicit (`fly.io/docs/.../machine-restart-policy`); volumes $0.15/GB (`fly.io/docs/about/pricing`) | shared-cpu-1x ~$1.94 + 10GB × $0.15 = **~$3.50–5/mo** | ✅ Cheaper fallback — needs a Dockerfile |
| Railway | Volumes + always-on work, but Hobby caps volumes at **5GB** (`docs.railway.com/volumes`, `/pricing/plans`) → forces Pro $20/mo for 10GB | **$20/mo flat** (usage ~$7.50 sits inside Pro credit) | ❌ Ruled out — 4× the cost for this shape |

- Decision: **Render** (operator confirms at deploy time; Fly.io documented as
  fallback in the runbook).

## Deliverable 2 — Packaging for the host
- Start command, disk mount (collector output dir), restart policy, install from
  existing `requirements.txt` (fastapi, uvicorn, requests, sse-starlette, anyio).
- Watchdog portability: `scripts/collector_watchdog.py` used Windows-only
  primitives (`Get-CimInstance` probe, `taskkill`, `DETACHED` flags). Done on
  this branch: POSIX `pgrep -f scripts.collect_ticks` probe, `SIGKILL` kill
  path, `COLLECT_OUT`/`COLLECT_EXTRA_ARGS` host overrides, `manifest_path()`
  follows the redirect — with **identical Windows behavior** (unknown-state ⇒
  no action, never double-start). Capture logic itself (poll interval,
  budgets, rotation, manifest schema) did NOT change.

## Deliverable 3 — 1+ hour proof capture on the host
- Watchdog + collector running, manifest `sampling_interval_s` sane (~1.4s warm,
  per `docs/operations.md`), no wedged events in `run/watchdog.log`.
- Day files downloadable; `--gzip` verified working if disk is tight
  (`.jsonl.gz` is first-class in verify and index).

## Deliverable 4 — Deploy runbook in docs
- Short runbook: deploy, check logs, restart, pull day files — with copy-paste
  commands. Day-file pull records `sha256` per file (the golden manifest needs
  one checksum per day — charter §3.1).

## Out of scope (explicit)
- Hosting the dashboard or live trader.
- Any change to collector capture logic or verify thresholds.
- The 5-day golden capture and certification itself (issue #281).
- #279 UI behavior; #174 socket books (frozen until golden capture completes).
