# SPEC — Issue #303: Tick Files summary cards and table layout

Source: [Issue #303](https://github.com/AI-Degen-69/crypto-spread/issues/303), checked against the current implementation.

## Goal
Rework the Tick Files display and its manifest payload so window counts are shown exactly by market duration, without estimates or unrelated behavior changes.

## Acceptance criteria
- Tick Files summary has no Tick Snapshots or Tape Entries cards.
- Market Windows shows dataset-wide 5m and 15m totals separately, using accepted verification data. `5m` means duration 300; `15m` means duration 900.
- The table has exactly this order: Last Modified, File Name, 5m / 15m, Integrity, Research Readiness, Actions, Size.
- The combined per-file count cell uses exact verification data. Missing verification is displayed as unknown, not as zero.
- The preferred-file badge and existing actions remain available in their respective cells.
- Changes stay within the Tick Files template and its `/api/ticks/manifest` payload mapping, plus focused tests.

## Explicit out of scope
- Collector, gates, verification algorithm, sidecar acceptance rules, and anything under `run/`.
- Golden Dataset card and backtest dropdown.
- Raw/pristine section split, PRISTINE badge, and delete protection (#295).
- Other dashboard tabs or diagnostic displays of snapshot and tape counts.

## Code-verified planning inputs
- `server/osc_dash.py` currently builds the manifest aggregate in `_aggregate_ticks`; it deduplicates copies by basename in `api_ticks_manifest` and merges sidecar `market_breakdown` by `(series, duration)`.
- The per-file manifest loop already exposes `market_breakdown` when a fingerprint-matched sidecar is accepted. `duration` is available for splitting the counts.
- Current aggregate `windows_source` is `cache`, `partial`, or `none`; keep that contract. For `partial`, show only the known subtotal (0 if no accepted cache is available yet); use `null` split totals for `none`.
- `loadManifest()` builds the cards and seven-column file table. Its empty row currently has `colspan="5"`; verification refresh currently selects readiness through `td:nth-child(6)`.
- The issue body is explicitly UI-only, yet exact counts are not currently in the API mapping. Add only the mapping fields required by the requested UI; do not alter verification or sidecar acceptance.
- #295 is closed, but its requested raw/pristine and deletion features remain explicitly outside this issue.
