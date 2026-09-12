"""Shadow EV pilot — paper-only validation of the EV-research winner.

Recommended config (ev-research-explained.html, winner section):
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

RUN_DIR = ROOT / "run" / "shadow_ev"


def build_engine(band: float, shares: int, starting_balance: float) -> LiveTraderEngine:
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
               starting_balance: float, outdir: Path,
               snap_every_sec: float) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    snap_file = outdir / "snapshots.jsonl"
    trades_file = outdir / "trades.jsonl"
    meta = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "config_hypothesis": {
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
        },
        "planned_hours": hours,
    }
    (outdir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

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
        final["stopped_utc"] = datetime.now(timezone.utc).isoformat()
        (outdir / "final.json").write_text(json.dumps(final, indent=2), encoding="utf-8")
        print(f"[shadow] DONE pnl={final['total_pnl']:+.2f} trades={final['total_trades']} "
              f"win={final['win_rate']}% pairs={final['pairs_merged']} "
              f"final -> {outdir / 'final.json'}", flush=True)
        return final


def main() -> None:
    ap = argparse.ArgumentParser(description="Shadow EV pilot (paper only)")
    ap.add_argument("--hours", type=float, default=11.0)
    ap.add_argument("--band", type=float, default=0.04)
    ap.add_argument("--shares", type=int, default=5)
    ap.add_argument("--starting-balance", type=float, default=1000.0)
    ap.add_argument("--snap-every", type=float, default=60.0)
    ap.add_argument("--outdir", type=str, default="")
    args = ap.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    outdir = Path(args.outdir) if args.outdir else (RUN_DIR / f"shadow_ev_{stamp}")
    asyncio.run(amain(args.hours, args.band, args.shares,
                      args.starting_balance, outdir, args.snap_every))


if __name__ == "__main__":
    main()
