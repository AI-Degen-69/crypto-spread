"""Shadow EV pilot — paper-only validation of the EV-research winner.

Recommended config (research-papers/abstract-and-methodology.html
in any paper run, winner section):
  delay 60s · band 0.03-0.04 · quote both sides at mid-0.03 ·
  no stop-loss · hold-to-settle · chase capped at pair cost 0.98 ·
  universe xrp-15m + bnb-15m + eth-5m · 5 shares/leg.

Paper mode only: never posts orders, never touches wallet keys.
Simulates fills off the live book exactly like the cockpit paper engine.
Logs per-minute snapshots with win-rate / pairs / exits / P&L so the
morning review has the full demand table.

Usage:
  python -m scripts.shadow_ev_pilot --hours 11          # detached shadow run
  python -m scripts.shadow_ev_pilot --hours 0.05        # 3-min smoke test
  python -m scripts.shadow_ev_pilot --hours 11 --band 0.03
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from strategy.live_trader import LiveTraderEngine, PATIENT_BAND_MAKER  # noqa: E402
from scripts import run_layout  # noqa: E402


def build_engine(band: float, shares: int, starting_balance: float) -> LiveTraderEngine:
    """Paper-only engine pinned to the hold-to-settle winner preset; refuses live/stop modes."""
    eng = LiveTraderEngine(load_persisted=False)
    # Named preset first (offset 0.03 / band 0.04 / delay 60s / no-stop /
    # max_pair_cost 0.98 / xrp15+bnb15+eth5 universe), then pin the
    # hold-to-settle extras the preset table does not carry:
    #  - naked_leg_timeout_pct=0  -> never force-exit an unpaired leg
    #  - max_reentries_per_window=0 -> no re-entry (research: negative)
    #  - entry_timeout_pct=1.0    -> never cancel unfilled entry quotes early
    #  - exit_reversal wide       -> mercy rule cannot fire without a stop
    eng.update_config(
        preset=PATIENT_BAND_MAKER,
        mode="paper",
        shares=shares,
        starting_balance=starting_balance,
        entry_band=band,  # allows 0.03 variant via CLI
        enable_leg_chase=True,
        naked_leg_timeout_pct=0.0,
        max_reentries_per_window=0,
        entry_timeout_pct=1.0,
        exit_reversal=0.50,
    )
    if eng.stop_loss_enabled is not False:
        raise RuntimeError("shadow pilot requires stop_loss_enabled=False")
    if eng.mode != "paper":
        raise RuntimeError("shadow pilot must run in paper mode — refusing to start")
    return eng


def snapshot(eng: LiveTraderEngine) -> dict:
    """Compact per-minute engine state (markets, PnL, win-rate, recent trades)."""
    st = eng.get_state()
    mkts = {}
    for slug, m in st.get("markets", {}).items():
        mkts[slug] = {
            "status": m.get("status"),
            "mid": m.get("mid"),
            "resting_up": m.get("resting_up"),
            "resting_down": m.get("resting_down"),
            "filled_up": m.get("filled_up"),
            "filled_down": m.get("filled_down"),
            "pair_captured": m.get("pair_captured"),
            "exit_taken": m.get("exit_taken"),
            "band_skip": m.get("band_skip"),
            "realized_pnl_usd": m.get("realized_pnl_usd"),
            "last_action": m.get("last_action"),
        }
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "is_running": st.get("is_running"),
        "mode": st.get("mode"),
        "active_preset": st.get("active_preset"),
        "params": st.get("params"),
        "portfolio_value": st.get("portfolio_value"),
        "total_pnl": st.get("total_pnl"),
        "realized_pnl": st.get("realized_pnl"),
        "unrealized_pnl": st.get("unrealized_pnl"),
        "win_rate": st.get("win_rate"),
        "total_trades": st.get("total_trades"),
        "pairs_merged": st.get("pairs_merged"),
        "stops_triggered": st.get("stops_triggered"),
        "reentry_stats": st.get("reentry_stats"),
        "band_skip_stats": st.get("band_skip_stats"),
        "markets": mkts,
        "trades": st.get("trades", [])[:50],
    }


async def amain(hours: float, band: float, shares: int,
               starting_balance: float, run_dir: Path,
               snap_every_sec: float) -> dict:
    """Run the paper engine, append data/ snapshots+trades, write papers/manifest/summary at stop."""
    data_dir = run_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "research-papers").mkdir(parents=True, exist_ok=True)
    snap_file = data_dir / "snapshots.jsonl"
    trades_file = data_dir / "trades.jsonl"
    # Manifest/summary reference these paths even for zero-hour runs.
    snap_file.touch()
    trades_file.touch()
    started_utc = datetime.now(timezone.utc)
    config_hypothesis = {
        "preset": PATIENT_BAND_MAKER,
        "offset": 0.03,
        "entry_band": band,
        "entry_delay_sec": 60.0,
        "stop_loss_enabled": False,
        "hold_to_settle": True,
        "naked_leg_timeout_pct": 0.0,
        "max_reentries_per_window": 0,
        "entry_timeout_pct": 1.0,
        "enable_leg_chase": True,
        "max_pair_cost": 0.98,
        "shares": shares,
        "starting_balance": starting_balance,
        "mode": "paper",
        "universe": ["xrp-up-or-down-15m", "bnb-up-or-down-15m", "eth-up-or-down-5m"],
    }
    meta = {
        "started_utc": started_utc.isoformat(),
        "config_hypothesis": config_hypothesis,
        "planned_hours": hours,
    }
    (data_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    # Lifecycle paper 1/3 at start (auto-generated stub; hand-authored
    # methodology lives in research-papers/abstract-and-methodology.html).
    run_layout.write_paper_stub(
        run_dir, "abstract-and-methodology",
        f"Abstract & methodology — {run_dir.name}",
        f"<p>preset={PATIENT_BAND_MAKER} mode=paper band={band} "
        f"shares={shares} planned_hours={hours}</p>"
        f"<pre>{json.dumps(config_hypothesis, indent=2)}</pre>",
    )

    eng = build_engine(band, shares, starting_balance)
    eng.start()
    print(f"[shadow] engine started mode={eng.mode} preset={eng.active_preset} "
          f"band={eng.entry_band} delay={eng.entry_delay_sec} "
          f"stop_loss={eng.stop_loss_enabled} shares={eng.shares}", flush=True)
    print(f"[shadow] snapshots -> {snap_file}", flush=True)

    deadline = time.time() + hours * 3600
    last_snap = 0.0
    seen_trade_ids: set[str] = set()
    try:
        while time.time() < deadline and eng.is_running:
            await asyncio.sleep(1.0)
            now = time.time()
            if now - last_snap >= snap_every_sec:
                last_snap = now
                snap = snapshot(eng)
                with open(snap_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(snap) + "\n")
                # append only new trades
                with open(trades_file, "a", encoding="utf-8") as f:
                    for t in snap["trades"]:
                        tid = str(t.get("id"))
                        if tid not in seen_trade_ids:
                            seen_trade_ids.add(tid)
                            f.write(json.dumps(t) + "\n")
                print(f"[shadow] {snap['ts'][11:19]} pnl={snap['total_pnl']:+.2f} "
                      f"real={snap['realized_pnl']:+.2f} trades={snap['total_trades']} "
                      f"win={snap['win_rate']}% pairs={snap['pairs_merged']} "
                      f"stops={snap['stops_triggered']} pv={snap['portfolio_value']:.2f}",
                      flush=True)
    except KeyboardInterrupt:
        print("[shadow] interrupted by user", flush=True)
    finally:
        eng.stop(stop_streams=True)
        final = snapshot(eng)
        stopped_utc = datetime.now(timezone.utc)
        final["stopped_utc"] = stopped_utc.isoformat()
        (data_dir / "final.json").write_text(json.dumps(final, indent=2), encoding="utf-8")
        # Lifecycle paper 2/3 at stop (auto-generated numbers + pointers —
        # NOT hand analysis; conclusions stay manual post-analysis).
        run_layout.write_paper_stub(
            run_dir, "results-and-findings",
            f"Results & findings — {run_dir.name}",
            f"<p>total_pnl={final['total_pnl']:+.2f} "
            f"realized={final['realized_pnl']:+.2f} trades={final['total_trades']} "
            f"win={final['win_rate']}% pairs={final['pairs_merged']} "
            f"stops={final['stops_triggered']}</p>"
            f"<p>source data: <a href=\"../data/final.json\">data/final.json</a>, "
            f"<a href=\"../data/trades.jsonl\">data/trades.jsonl</a></p>",
        )
        papers = {
            "abstract_and_methodology": "research-papers/abstract-and-methodology.html",
            "results_and_findings": "research-papers/results-and-findings.html",
            "conclusions_and_projections": "research-papers/conclusions-and-projections.html",
        }
        data_list = ["data/meta.json", "data/snapshots.jsonl",
                     "data/trades.jsonl", "data/final.json"]
        run_layout.write_summary_html(run_dir, {
            "title": f"Shadow EV pilot — {run_dir.name}",
            "run_id": run_dir.name,
            "kind": "paper",
            "started_local": started_utc.astimezone().isoformat(),
            "config_hypothesis": config_hypothesis,
            "papers": papers,
            "data": data_list,
        })
        run_layout.write_manifest(run_dir, {
            "kind": "paper",
            "run_id": run_dir.name,
            "started_local": started_utc.astimezone().isoformat(),
            "started_utc": started_utc.isoformat(),
            "stopped_utc": stopped_utc.isoformat(),
            "tz": run_layout.local_tz_abbr(),
            "preset": PATIENT_BAND_MAKER,
            "config_hypothesis": config_hypothesis,
            "planned_hours": hours,
            "final": {k: final[k] for k in (
                "total_pnl", "realized_pnl", "total_trades",
                "win_rate", "pairs_merged", "stops_triggered")},
            "data": data_list,
            "papers": papers,
            "summary": "summary.html",
        })
        print(f"[shadow] DONE pnl={final['total_pnl']:+.2f} trades={final['total_trades']} "
              f"win={final['win_rate']}% pairs={final['pairs_merged']} "
              f"final -> {data_dir / 'final.json'}", flush=True)
    # Outside finally: a loop exception must propagate (Ruff B012), not be swallowed.
    return final


def main() -> None:
    """CLI: resolve the runs/paper dir (or --outdir escape hatch) and run amain."""
    ap = argparse.ArgumentParser(description="Shadow EV pilot (paper only)")
    ap.add_argument("--hours", type=float, default=11.0)
    ap.add_argument("--band", type=float, default=0.04)
    ap.add_argument("--shares", type=int, default=5)
    ap.add_argument("--starting-balance", type=float, default=1000.0)
    ap.add_argument("--snap-every", type=float, default=60.0)
    ap.add_argument("--outdir", type=str, default="")
    args = ap.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    if args.outdir:
        # Escape hatch (tests): same layout rooted inside the given dir.
        run_dir = Path(args.outdir)
        (run_dir / "data").mkdir(parents=True, exist_ok=True)
        (run_dir / "research-papers").mkdir(parents=True, exist_ok=True)
    else:
        now_local = datetime.now().astimezone()
        run_dir = run_layout.new_run_dir(
            "paper", now_local, run_layout.local_tz_abbr())
    print(f"[shadow] run dir -> {run_dir} (stamp {stamp})", flush=True)
    asyncio.run(amain(args.hours, args.band, args.shares,
                      args.starting_balance, run_dir, args.snap_every))


if __name__ == "__main__":
    main()
