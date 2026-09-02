"""Interactive script to buy 1 leg, let it fill, and observe live Stop-Loss exit."""
from __future__ import annotations
import argparse
import datetime
import os
import sys
import time

from strategy.live_trader import (
    get_live_trader_engine,
    fetch_live_series_market,
    fetch_polymarket_account_value,
    _load_env_file,
    CLOB_HOST,
)
from strategy.markets import full_book


def main():
    """Execute live buy fill and stop-loss exit demonstration."""
    parser = argparse.ArgumentParser(description="Live Buy Fill and Stop-Loss Exit Demonstration")
    parser.add_argument("--series", default="btc-up-or-down-5m", help="Series slug (default: btc-up-or-down-5m)")
    parser.add_argument("--side", choices=["UP", "DOWN"], default="UP", help="Which outcome token to buy (UP or DOWN)")
    parser.add_argument("--size", type=float, default=5.0, help="Number of shares (default: 5 - Polymarket minimum)")
    parser.add_argument("--exit-thresh", type=float, default=0.02, help="Stop loss drift threshold in cents (default: $0.02)")
    parser.add_argument("--max-wait-sec", type=int, default=30, help="Max seconds to wait before safety exit (default: 30s)")
    args = parser.parse_args()

    _load_env_file()
    print("=" * 80)
    print(" [*] POLYMARKET LIVE STOP-LOSS FILL & EXIT EXPERIMENT")
    print("=" * 80)

    engine = get_live_trader_engine()
    engine.mode = "live"

    wallet = engine.wallet_address or os.getenv("POLY_FUNDER") or os.getenv("RELAYER_API_KEY_ADDRESS") or ""
    print(f"\n[1/5] Checking account credentials for wallet: {wallet}")
    acc = fetch_polymarket_account_value(wallet)
    print(f"      - Cash Collateral: ${acc.get('cash_balance', 0.0):.2f}")
    print(f"      - Net Value:       ${acc.get('net_value', 0.0):.2f}")

    print(f"\n[2/5] Fetching active market for {args.series}...")
    market = fetch_live_series_market(args.series)
    if not market:
        print("[!] Error: No active market open.")
        return 1

    slug = market.get("slug", "")
    token_id = market.get("up_token") if args.side == "UP" else market.get("down_token")
    end_ts = market.get("end_ts", 0.0)
    rem_sec = max(0.0, end_ts - time.time())
    if rem_sec < 45:
        wait_for_next = int(rem_sec) + 3
        print(f"      [!] Only {int(rem_sec)}s remaining in current window.")
        print(f"      Waiting {wait_for_next}s for the next fresh 5m window to open safely...")
        time.sleep(wait_for_next)
        market = fetch_live_series_market(args.series)
        if not market:
            print("[!] Error: Next market not available.")
            return 1
        slug = market.get("slug", "")
        token_id = market.get("up_token") if args.side == "UP" else market.get("down_token")
        end_ts = market.get("end_ts", 0.0)
        rem_sec = max(0.0, end_ts - time.time())

    book = full_book(CLOB_HOST, token_id)
    best_bid = book.get("best_bid")
    best_ask = book.get("best_ask")

    print(f"      - Market:          {slug}")
    print(f"      - Time Remaining:  {int(rem_sec)}s")
    print(f"      - Leg to Buy:      {args.side}")
    print(f"      - Token ID:        {token_id}")
    print(f"      - Best Bid:        ${best_bid or 0.0:.2f}")
    print(f"      - Best Ask:        ${best_ask or 0.0:.2f}")

    if best_ask is None:
        print("[!] Error: No ask liquidity available in the order book.")
        return 1

    # Buy at Best Ask for immediate fill
    buy_price = best_ask
    total_cost = buy_price * args.size

    print(f"\n[3/5] Placing immediate BUY order for {args.size} shares @ ${buy_price:.2f} (Total: ${total_cost:.2f})...")
    res = engine.place_live_quote(token_id=token_id, price=buy_price, size=args.size, side="BUY")
    order_id = res.get("order_id")
    if not order_id:
        print(f"[!] Placement failed: {res}")
        return 1

    print(f"[OK] Order submitted! Order ID: {order_id}")

    # Confirm fill on CLOB
    clob_client = engine.get_clob_client()
    filled = False
    print("     Verifying fill on CLOB...")
    for _ in range(10):
        time.sleep(1)
        ord_info = clob_client.get_order(order_id)
        status = (ord_info.get("status") or "").upper()
        size_matched = float(ord_info.get("size_matched", 0.0) or 0.0)
        if status in ("MATCHED", "FILLED") or size_matched >= args.size:
            filled = True
            print(f"[OK] FILL CONFIRMED: Bought {args.size} {args.side} shares @ ${buy_price:.2f}!")
            break

    if not filled:
        print(f"[!] Order not filled immediately (status={status}). Cancelling for safety...")
        engine.cancel_live_order(order_id)
        return 1

    # Monitor for stop loss
    print(f"\n[4/5] Monitoring Position: Stop-Loss threshold = ${args.exit_thresh:.2f} adverse drift (or after {args.max_wait_sec}s)...")
    entry_mid = (best_bid + best_ask) / 2.0 if best_bid and best_ask else buy_price
    start_time = time.time()
    exit_triggered = False
    exit_reason = ""

    while time.time() - start_time < args.max_wait_sec:
        time.sleep(1.0)
        cur_book = full_book(CLOB_HOST, token_id)
        cur_bid = cur_book.get("best_bid")
        cur_ask = cur_book.get("best_ask")
        if cur_bid is None or cur_ask is None:
            continue
        cur_mid = (cur_bid + cur_ask) / 2.0
        drift = entry_mid - cur_mid  # Positive drift means price dropped against us
        elapsed = int(time.time() - start_time)

        print(f"      [{elapsed:2d}s] Mid: ${cur_mid:.3f} | Best Bid: ${cur_bid:.2f} | Drift vs 0.50: {drift:+.3f} (stop threshold: ${args.exit_thresh:.2f})", flush=True)

        if drift >= args.exit_thresh:
            exit_triggered = True
            exit_reason = f"Adverse price drift ({drift:.3f} >= {args.exit_thresh:.2f})"
            break

    if not exit_triggered:
        exit_reason = f"Safety timeout ({args.max_wait_sec}s elapsed) — demonstration exit"

    print(f"\n[5/5] STOP-LOSS TRIGGERED: {exit_reason}!")
    # Get freshest bid
    cur_book = full_book(CLOB_HOST, token_id)
    raw_bid = cur_book.get("best_bid") or (buy_price - 0.05)
    sell_price = round(max(0.01, min(0.99, float(raw_bid))), 2)

    print(f"      Sending immediate SELL order: {args.size} shares @ ${sell_price:.2f}...")
    sell_res = engine.place_live_quote(token_id=token_id, price=sell_price, size=args.size, side="SELL")
    sell_id = sell_res.get("order_id")
    print(f"      Sell Order ID: {sell_id} (status: {sell_res.get('status')})")

    # Verify sell fill
    for _ in range(8):
        time.sleep(1)
        sell_info = clob_client.get_order(sell_id)
        st = (sell_info.get("status") or "").upper()
        if st in ("MATCHED", "FILLED") or float(sell_info.get("size_matched", 0.0) or 0.0) >= args.size:
            print(f"[OK] SELL FILL CONFIRMED: Sold {args.size} shares @ ${sell_price:.2f}!")
            break

    pnl = (sell_price - buy_price) * args.size
    print("\n" + "=" * 80)
    print(" [*] STOP-LOSS EXPERIMENT SUMMARY")
    print("=" * 80)
    print(f" - Bought:        {args.size} shares @ ${buy_price:.2f} (${buy_price * args.size:.2f})")
    print(f" - Sold:          {args.size} shares @ ${sell_price:.2f} (${sell_price * args.size:.2f})")
    print(f" - Realized PnL:  {pnl:+.2f}$ (exit protected capital)")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
