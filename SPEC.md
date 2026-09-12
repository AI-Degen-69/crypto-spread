# SPEC.md — Issue #147: run-folders layout (Stage 1, no entrypoint unification)

## 1. Goal
Every paper/live run becomes one self-contained folder under a paper/live
safety split, with a fixed naming + layout convention, so a run's data,
config, papers and summary travel together and a future script can scan
`/runs` into a comparison table without opening HTML.

## 2. Convention (agreed 2026-09-12, locked by this issue)
```
/runs/paper|live/YYYY-MM-DD_HH-MM_TZ/{data/,research-papers/,summary.html,manifest.json}
```
- `YYYY-MM-DD_HH-MM`: 24h local operator time, no colons (illegal on Windows), sortable. Year mandatory.
- `TZ`: local zone abbreviation from the machine (`IDT`/`IST` for Israel).
- `paper/` = SIMULATION/PAPER (no real money). `live/` = real orders, real money on Polymarket. The split is a safety boundary, not cosmetics.
- `data/`: trades, raw/polled data, ticks, jsons, jsonl (`meta.json`, `snapshots.jsonl`, `trades.jsonl`, `final.json`, run log tail).
- `research-papers/` (created at run start): `abstract-and-methodology.html` (written at start), `results-and-findings.html` (after finish/stop), `conclusions-and-projections.html` (post-run, after digesting the data).
- `summary.html` (run level, written at start): run config, basic info, pointers to data + papers.
- `manifest.json` (run level, machine-readable): params, config, final PnL, data paths.
- Full spec: `docs/run-conventions.md` (written in this issue).
- `runs/` is gitignored like `run/` (user decision). The migrated 11h run is a local example, not a committed artifact.
- Artifact taxonomy: `runs/` papers are **per-run**; **per-issue** HTML artifacts (Station 4 showcases, research explainers) live at `docs/issues/<id>-<kind>-<slug>.html` — e.g. `docs/issues/147-showcase-run-folders.html`, where `<kind>` ∈ {showcase, explained} and `<slug>` derives from the issue title. Canonical workflow HTML artifacts ALWAYS live under `docs/` with the issue number in the path — never in agent-local scratch (`.freebuff/`, `%TEMP%` is transient preview only, never the home). `.freebuff/` stays scratch source. Same rule is mirrored in the global `explain-issue` skill (user decision).

## 3. In Scope
1. `docs/run-conventions.md` with the convention above.
2. One-shot migration of the 11h run (`run/shadow_ev/shadow_ev_20260911_221054/` + the two `docs/reports/` HTMLs) into `runs/paper/2026-09-11_22-10_IDT/` as the first example; hard cut (user decision), no back-compat shim.
3. New helper `scripts/run_layout.py` (single source of truth for naming + manifest schema + paper stubs) + `scripts/shadow_ev_pilot.py` writes the new layout directly (abstract at start, results stub at stop, conclusions stay manual post-analysis). `--outdir` escape hatch kept for tests.
4. Path-ref updates: `.gitignore` (+`runs/`), `AGENTS.md`, `README.md`, `docs/operations.md`, plus in `docs/issue-workflow.md`: Station 4 output path becomes `docs/issues/<id>-<kind>-<slug>.html`, §6 prune paths updated to match, and a hardened artifact-home rule (agent-local scratch like `.freebuff/` is never the canonical home). Same scheme applied to the global skill `~/.agents/skills/explain-issue/SKILL.md` (+ `reference.md:15`). `server/osc_dash.py` has no `shadow_ev`/`reports` refs (verified by grep) — verify-only, no logic change.
5. Tests: new `tests/test_run_layout.py` (naming, kind validation, manifest schema, stub writers) + pilot smoke test into a tmp dir.

## 4. Migration mapping (11h run → `runs/paper/2026-09-11_22-10_IDT/`)
| Source | Destination | Note |
|---|---|---|
| `run/shadow_ev/shadow_ev_20260911_221054/{meta,snapshots,trades,final}.json*` | `data/` | byte-identical, hash-verified before delete |
| `run/shadow_ev_pilot.log` (tail) | `data/shadow_ev_pilot.log` | evidence for the DONE line |
| `docs/reports/ev_research_2026-09-11_explained.html` | `research-papers/abstract-and-methodology.html` | backtest research = pre-run methodology basis; + banner with canonical path + pilot config pointer |
| `docs/reports/shadow_ev_2026-09-11_showcase.html` | `research-papers/results-and-findings.html` | live running results; fix stale `run/shadow_ev/…` + `.freebuff/…` refs to `data/` + canonical papers |
| showcase §§ what-if + next-steps + verify | `research-papers/conclusions-and-projections.html` (new) | derived, not copied whole |
| (generated) | `summary.html`, `manifest.json` | from §6 schema |
| `run/shadow_ev/shadow_ev_20260911_220912/` | DELETE | aborted start (0-byte trades, 7KB snapshots); drop as the "empty run folder" |
| `run/shadow_ev/shadow_ev_20260911_221054/`, `run/shadow_ev/` (if empty), the two `docs/reports/` HTMLs | DELETE | after hash verification; hard cut |

## 5. Out of Scope (moved to #148 / not this issue)
- Unifying `bot/paper_bot.py` vs `strategy/live_trader.py` vs bankroll scripts.
- Changing quoting logic, pricing, or risk rules. Rewriting the cockpit engine.
- Deleting archived tick/oscillation data. Touching `run/ticks/`, `run/sweeps/`, `run/observations/`.

## 6. Interfaces (locked before logic)
- `scripts/run_layout.py`:
  - `RUNS_ROOT = ROOT / "runs"`
  - `new_run_dir(kind: Literal["paper","live"], start: datetime, tz_abbr: str) -> Path` — creates `runs/{kind}/YYYY-MM-DD_HH-MM_TZ/` with `data/` + `research-papers/`; raises `ValueError` on any other kind (safety boundary is code, not just docs).
  - `write_manifest(run_dir, payload: dict) -> Path` — schema keys: `kind, run_id, started_local, started_utc, stopped_utc, tz, preset, config_hypothesis, planned_hours, final{total_pnl, realized_pnl, total_trades, win_rate, pairs_merged, stops_triggered}, data[...], papers{abstract_and_methodology, results_and_findings, conclusions_and_projections}, summary`.
  - `write_summary_html(run_dir, ctx: dict) -> Path` — config + basic info + relative pointers to data + papers.
  - `write_paper_stub(run_dir, name, title, body_html) -> Path` — minimal themed HTML shell for abstract-at-start / results-at-stop.
- `scripts/shadow_ev_pilot.py`: `RUN_DIR` → `run_layout.new_run_dir("paper", local_now, local_tz)`; all of `meta/snapshots/trades/final` → `data/`; abstract stub at start, results stub (numbers table from `final` + data pointers) at stop.
- No dashboard API change. No new dependencies (stdlib only).

## 7. Acceptance Criteria
- [ ] `docs/run-conventions.md` exists and matches this spec.
- [ ] 11h run fully migrated per §4; old paths gone; empty run folder removed.
- [ ] New pilot runs land directly in the layout (abstract at start, results stub at stop, conclusions manual).
- [ ] All path references resolve (`pytest -q` green, 0 failures; grep for `run/shadow_ev` in `*.py`/`*.md` returns only historical docs lines).
