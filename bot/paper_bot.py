"""[DEPRECATED] Paper bot — SPREAD-2 legacy evaluation script.

WARNING: This entrypoint is deprecated and unmaintained.
Use `strategy.live_trader.LiveTraderEngine` (canonical trading engine)
or `scripts.shadow_ev_pilot` (for paper EV runs) instead.
Live order execution is disabled in this script.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import warnings
from pathlib import Path

warnings.warn(
    "bot.paper_bot is deprecated and superseded by strategy.live_trader.LiveTraderEngine "
    "and scripts.shadow_ev_pilot. Do not use for new execution runs.",
    DeprecationWarning,
    stacklevel=2,
)
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from strategy.markets import fetch_live_market, full_book
from strategy.series import SERIES

GAMMA_HOST = os.getenv("GAMMA_HOST", "https://gamma-api.polymarket.com")
CLOB_HOST = os.getenv("CLOB_HOST", "https://clob.polymarket.com")
SPREAD_OFFSET = 0.02  # 2¢ below mid on both sides -> 0.96 pair cost (4¢ profit on merge)
MIN_T_REMAINING_SEC = 15.0
DECIDED_PRICE = 0.02


def paper_poll(live: bool = False):
    """Evaluate live market quotes for SPREAD-2 entry."""
    would = 0
    blocked_price = 0
    blocked_trem = 0
    total = 0

    for slug, dur, label in SERIES:
        m = fetch_live_market(GAMMA_HOST, slug)
        if not m:
            continue
        total += 1

        t_rem = m.t_remaining()
        if t_rem < MIN_T_REMAINING_SEC:
            blocked_trem += 1
            print(f"  {label:9} SKIP t_rem {t_rem:.0f}s <{MIN_T_REMAINING_SEC:.0f}s  {m.market_slug}")
            continue

        ub = full_book(CLOB_HOST, m.up_token)
        db = full_book(CLOB_HOST, m.down_token)

        # Mid calculation from UP book
        mid = None
        if ub["best_bid"] is not None and ub["best_ask"] is not None:
            mid = (ub["best_bid"] + ub["best_ask"]) / 2
        elif ub["best_bid"] is not None:
            mid = ub["best_bid"] + 0.005
        elif ub["best_ask"] is not None:
            mid = ub["best_ask"] - 0.005

        if mid is None:
            print(f"  {label:9} no mid (book empty) {m.market_slug}")
            continue

        if mid < DECIDED_PRICE or mid > 1 - DECIDED_PRICE:
            blocked_price += 1
            print(f"  {label:9} SKIP decided mid {mid:.3f} {m.market_slug}")
            continue

        # SPREAD-2: Resting limit bids at mid - 0.02 on both legs
        bid_up = round(mid - SPREAD_OFFSET, 3)
        bid_down = round((1.0 - mid) - SPREAD_OFFSET, 3)
        pair_cost = round(bid_up + bid_down, 3)

        spread_up = round(ub["best_ask"] - ub["best_bid"], 3) if ub["best_bid"] is not None and ub["best_ask"] is not None else None
        spread_str = f"{spread_up:.3f}" if spread_up is not None else "-"

        would += 1
        print(f"  {label:9} WOULD quote UP {bid_up:.3f} | DOWN {bid_down:.3f} | pair {pair_cost:.2f} | mid {mid:.3f} | spread {spread_str} | t_rem {t_rem:.0f}s")

        if live:
            # Placeholder for order placement if live mode enabled
            pass

    print(f"SUMMARY polled {total} live, would_quote {would}, blocked price{blocked_price} t_rem{blocked_trem}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="SPREAD-2 Paper Bot")
    ap.add_argument("--minutes", type=float, default=2.0, help="Run duration in minutes")
    ap.add_argument("--live", action="store_true", help="If set, place live signed orders (requires funding)")
    args = ap.parse_args()

    if args.live:
        sys.stderr.write(
            "ERROR: Live execution is disabled in deprecated bot/paper_bot.py.\n"
            "Use the canonical trading engine `strategy.live_trader.LiveTraderEngine` "
            "or the dashboard cockpit (:8802) for real money trading.\n"
        )
        sys.exit(1)

    print("WARNING: bot/paper_bot.py is DEPRECATED and unmaintained.")
    print("Use strategy.live_trader.LiveTraderEngine or scripts.shadow_ev_pilot instead.\n")

    end = time.time() + args.minutes * 60
    print(f"PAPER bot start {args.minutes} min | SPREAD_OFFSET={SPREAD_OFFSET} -> resting_pair 0.96 (4c merge profit)")

    n = 0
    while time.time() < end:
        n += 1
        print(f"\n--- poll {n} {time.strftime('%H:%M:%S')} ---")
        try:
            paper_poll(live=args.live)
        except Exception as e:
            print("poll err", e)
            import traceback; traceback.print_exc(limit=2)
        time.sleep(1.0)
    print("done")
