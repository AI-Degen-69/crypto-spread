# Run Conventions — `/runs` layout (issue #147)

Every paper/live run is one self-contained folder. A future script can scan
`/runs` into a comparison table from `manifest.json` files without opening HTML.

## Layout

```
/runs/paper|live/YYYY-MM-DD_HH-MM_TZ/{data/,research-papers/,summary.html,manifest.json}
```

- `YYYY-MM-DD_HH-MM`: 24h local operator time, no colons (illegal on Windows), sortable. Year mandatory.
- `TZ`: local zone abbreviation from the machine (`IDT`/`IST` for Israel).
- `paper/` = SIMULATION/PAPER (no real money). `live/` = real orders, real money on Polymarket. **The split is a safety boundary, not cosmetics** — enforced in code (`scripts/run_layout.py:new_run_dir` raises on any other kind).
- `data/`: trades, raw/polled data, ticks, jsons, jsonl (`meta.json`, `snapshots.jsonl`, `trades.jsonl`, `final.json`, run log).
- `research-papers/` (created at run start):
  - `abstract-and-methodology.html` — written when the run crosses the start line.
  - `results-and-findings.html` — after finish/stop.
  - `conclusions-and-projections.html` — after digesting the data, post-run only.
- `summary.html` (run level, written at start): run config, basic info, pointers to data + papers.
- `manifest.json` (run level, machine-readable): params, config, final PnL, data paths. Schema keys: `kind, run_id, started_local, started_utc, stopped_utc, tz, preset, config_hypothesis, planned_hours, final{total_pnl, realized_pnl, total_trades, win_rate, pairs_merged, stops_triggered}, data[...], papers{abstract_and_methodology, results_and_findings, conclusions_and_projections}, summary`.

`runs/` is gitignored (like `run/`). Runs are local evidence, not committed artifacts.

## Lifecycle (who writes what, when)

| Stage | Writer | Output |
|---|---|---|
| Run starts | runner (`shadow_ev_pilot.py` today) | `data/meta.json`, abstract stub, `summary.html` skeleton |
| Run stops | runner | `data/final.json`, results stub (auto numbers + pointers — NOT hand analysis), full `manifest.json` + `summary.html` |
| Post-analysis | human/agent by hand | `conclusions-and-projections.html`, and (optionally) replacing stubs with authored papers |

## Artifact taxonomy (global rule)

Three homes, three purposes — never mixed:

1. `runs/.../research-papers/` — **per-run** papers (methodology, results, conclusions of one run).
2. `docs/issues/<id>-<kind>-<slug>.html` — **per-issue** HTML artifacts from the issue workflow (Station 4 showcases, research explainers). `<kind>` ∈ {showcase, explained}, `<slug>` from the issue title. Same scheme is mirrored in the global `explain-issue` skill.
3. `.freebuff/`, `%TEMP%` — **scratch / transient preview only**. Never the canonical home of anything. (No doc or skill ever mandated `.freebuff/` — it grew organically as gitignored scratch.)

## First example

`runs/paper/2026-09-11_22-10_IDT/` — the 11h paper run (`patient_band_maker`, +$6.74 realized, 66 trades, 53 pairs, 97% win, 0 stops), migrated hash-verified from `run/shadow_ev/` + `docs/reports/` (issue #147, hard cut).
