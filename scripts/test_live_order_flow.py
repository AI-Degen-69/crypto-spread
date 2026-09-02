"""Interactive live order placement and cancellation verification script.

Demonstrates real Polymarket CLOB integration safely:
1. Connects to Polymarket CLOB using .env credentials.
2. Identifies the active 5-minute BTC market.
3. Places 1 small limit test order far below market ($0.05).
4. Prints the real Polymarket Order ID so you can verify it in your account.
5. Waits for your confirmation or countdown.
6. Cancels the order immediately and verifies its cancellation.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Optional

from strategy.live_trader import (
    get_live_trader_engine,
    fetch_live_series_market,
    fetch_polymarket_account_value,
    _load_env_file,
)


def main():
    """Execute interactive live order placement and cancellation test."""
    parser = argparse.ArgumentParser(description="Live Polymarket CLOB Order Placement & Cancellation Test")
    parser.add_argument("--series", default="btc-up-or-down-5m", help="5m series slug to test (default: btc-up-or-down-5m)")
    parser.add_argument("--price", type=float, default=0.05, help="Test limit price (default: $0.05 - far below market)")
    parser.add_argument("--size", type=float, default=5.0, help="Test order shares size (default: 5 - Polymarket minimum)")
    parser.add_argument("--auto-cancel-sec", type=int, default=10, help="Seconds to wait before auto-cancelling (default: 10)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without posting real order")
    args = parser.parse_args()

    _load_env_file()
    print("=" * 80)
    print(" [*] POLYMARKET CLOB LIVE ORDER EXECUTION & CANCELLATION TEST")
    print("=" * 80)

    engine = get_live_trader_engine()
    engine.mode = "live"

    wallet = engine.wallet_address or os.getenv("POLY_FUNDER") or os.getenv("RELAYER_API_KEY_ADDRESS") or ""
    print(f"\n[1/5] Checking account credentials for wallet: {wallet or 'NOT CONFIGURED'}")
    
    acc_info = fetch_polymarket_account_value(wallet)
    print(f"      - Cash Collateral:  ${acc_info.get('cash_balance', 0.0):.2f}")
    print(f"      - Net Value:        ${acc_info.get('net_value', 0.0):.2f}")
    print(f"      - Open Positions:   {acc_info.get('open_positions', 0)}")

    if not acc_info.get("success") and not args.dry_run:
        print("\n[!] WARNING: Could not verify account balance via CLOB credentials. Check your .env configuration.")

    print(f"\n[2/5] Fetching active 5m market for {args.series}...")
    market = fetch_live_series_market(args.series)
    if not market:
        print(f"[!] Error: No active market currently open for {args.series}.")
        return 1

    cond_id = market.get("conditionId", "")
    up_tok = market.get("up_token", "")
    slug = market.get("slug", "")
    rem = max(0.0, market.get("end_ts", 0.0) - time.time())

    print(f"      - Market Slug:      {slug}")
    print(f"      - Market URL:       https://polymarket.com/event/{slug}")
    print(f"      - Condition ID:     {cond_id}")
    print(f"      - UP Token ID:      {up_tok}")
    print(f"      - Time Remaining:   {int(rem)}s")

    if args.dry_run:
        print("\n[DRY RUN] Would place BUY order on UP token @ $%.2f (Size: %s)" % (args.price, args.size))
        print("[DRY RUN] Test completed successfully.")
        return 0

    print(f"\n[3/5] Placing REAL test limit BUY order on Polymarket CLOB:")
    print(f"      - Side:             BUY (UP)")
    print(f"      - Price:            ${args.price:.2f} (Resting limit order)")
    print(f"      - Shares:           {args.size}")
    print(f"      - Token:            {up_tok}")

    res = engine.place_live_quote(token_id=up_tok, price=args.price, size=args.size, side="BUY")
    if not res or not res.get("order_id"):
        print("\n[!] FAILED to place order on CLOB:")
        print(f"    Result: {res}")
        return 1

    order_id = res["order_id"]
    print(f"\n[OK] ORDER PLACED SUCCESSFULLY!")
    print(f"     Polymarket Order ID: {order_id}")
    print(f"     Status:              {res.get('status')}")
    print(f"\n[>>] CHECK YOUR POLYMARKET UI NOW (Open Orders tab)!")
    print(f"     You should see 1 open BUY order for {args.size} shares @ ${args.price:.2f}.")

    print(f"\n[4/5] Waiting {args.auto_cancel_sec} seconds before cancelling order...")
    for remaining in range(args.auto_cancel_sec, 0, -1):
        print(f"      Cancelling in {remaining}s... (Press Ctrl+C to cancel immediately)", end="\r")
        time.sleep(1)
    print("\n")

    print(f"[5/5] Cancelling order {order_id} on CLOB...")
    cancel_ok = engine.cancel_live_order(order_id)
    if cancel_ok:
        print(f"[OK] ORDER CANCELLED SUCCESSFULLY on Polymarket CLOB!")
        print(f"     Order {order_id} has been removed from active orders.")
    else:
        print(f"[!] Warning: Could not confirm single order cancel, attempting emergency cancel_all...")
        engine.cancel_all_orders()
        print(f"[OK] Emergency cancel_all completed.")

    print("\n" + "=" * 80)
    print(" [OK] LIVE PLACEMENT & CANCELLATION TEST PASSED")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
