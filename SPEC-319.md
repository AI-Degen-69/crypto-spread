# SPEC — Issue #319: Jungle King dashboard tab

The OFAT parameter-range manifest (`research/jungle-king/`) becomes a read-only
dashboard tab so the operator stops opening files for quick reference.

## Goal

A "Jungle King" tab in the canonical dashboard (`server/osc_dash.py`, :5515)
that renders all **19 parameters** from `research/jungle-king/param_ranges.json`
in one glanceable screen: manifest grouping, prominent baseline, scannable
candidate values, and the param class (tuning / structural / assumption) of
every parameter.

## Interface contract (locked)

New endpoint `GET /api/jungle-king` — 200 JSON:

```
{
  "groups": [                                  # manifest order, exactly 4
    {
      "key": "trading_knobs" | "exit_thresholds" | "structural_limits" | "execution_assumptions",
      "title": str,
      "params": [
        {
          "name": str,                         # manifest key, e.g. "offset",
                                               # "exit_thresh_by_slug.btc-up-or-down-5m"
          "label": str,                        # registry label when shared; manifest-derived otherwise
          "unit": str | null,
          "param_class": "tuning" | "structural" | "assumption",
          "baseline": number,
          "values": [number, ...],             # manifest order preserved
          "baseline_in_values": bool,
          "registry": {                        # subset of /api/params/spec entry, or null
            "label": str, "why": str, "default": number,
            "bounds": [low, high] | null
          }
        }
      ]
    }
  ]
}
```

- The endpoint reads `research/jungle-king/param_ranges.json` per request (file
  is ~368 lines; no cache machinery). Missing/corrupt file → JSON error body +
  non-200, never a traceback.
- The param_class/label/unit join is **server-side**. The client renders only
  from this payload — no second label copy in JS.
- Per-slug exit keys (`exit_thresh_by_slug.*`) inherit the class of the
  registry's parent key `exit_thresh_by_slug` (tuning).
- Frontend anchors: sidebar button `tab-btn-jungleking`, content container
  `id="tab-jungleking" class="tab-content"`, hook in `switchTab()`:
  `if(name==='jungleking') loadJungleKing();`. Sidebar position: after Backtest
  Sweeper, before Stats Summary.

## Acceptance criteria

1. All 19 manifest parameters render, grouped per the manifest's four sections.
2. Every parameter shows its baseline prominently and marks it inside the value
   row; candidate values are chips/pills (scannable), not checkboxes.
3. Each parameter shows its class (tuning / structural / assumption) consistent
   with the registry (`BacktestParams.param_spec()`).
4. Strictly read-only: no control triggers a run/sweep/edit; the tab holds no
   mutation path.
5. Empty/error state renders an inline notice (mirrors the dashboard's
   empty-state convention), never a blank screen.

## Edge cases

- Missing or malformed `param_ranges.json` → graceful JSON error + UI notice.
- A baseline not present in the manifest's own `values` array → still rendered
  (badge), flagged via `baseline_in_values: false`.
- Non-numeric policy knobs (`dead_zone_unit`, `naked_leg_at_expiry`) exist only
  in the README checklist, not in `param_ranges.json` — they are out of scope;
  the tab renders exactly the 19 JSON keys.

## Explicitly out of scope

- Running OFAT sweeps / backtests from the tab; no wiring into
  `scripts/sweep_backtest.py` or `/api/backtest`.
- Writing or editing the manifest from the UI.
- Any change to `param_ranges.json`, `BacktestParams`, or the `/api/params/spec`
  contract (including the params-hash contract).
- Charts of sweep results (none exist yet — the manifest defines what can be
  tested, not what was learned).
- Trading engine or execution-mode changes.

## Baseline provenance guard (do NOT "fix")

Manifest baselines for `queue_gate` (50.0) and `quote_shares` (120) mirror the
operator-replicable CLI defaults (`scripts/backtest.py:51` `default=50.0`,
`:58` `default=120`) and the README checklist — **not** the engine defaults
(`backtest/engine.py:116-117`: `queue_gate=0.0`, `quote_shares=5`). The viewer
presents the manifest as-is; aligning baselines to engine defaults is a defect,
not a cleanup.
