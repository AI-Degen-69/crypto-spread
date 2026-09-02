"""Test script for advance pre-quoting: places 2 limit orders (UP and DOWN)
on the upcoming 5-minute market (T+1), displays both in Polymarket, and cancels both.
"""
import argparse
import os
import sys
import time

from strategy.live_trader import (
    get_live_trader_engine,
    fetch_polymarket_account_value,
    fetch_live_and_upcoming_markets,
    _load_env_file,
)


def main():
    """Execute advance pre-quoting test on upcoming window."""
    parser = argparse.ArgumentParser(description="Test advance pre-quoting pair on upcoming market")
    parser.add_argument("--series", default="btc-up-or-down-5m", help="Series slug")
    parser.add_argument("--price", type=float, default=0.05, help="Test limit price (default: $0.05)")
    parser.add_argument("--size", type=float, default=5.0, help="Order size in shares (default: 5.0)")
    parser.add_argument("--auto-cancel-sec", type=int, default=45, help="Seconds before auto-cancel")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without CLOB call")
    args = parser.parse_args()

    _load_env_file()
    print("=" * 80)
    print(" [*] ADVANCE PRE-QUOTING TEST: 2 ORDERS (UP + DOWN) ON UPCOMING MARKET (T+1)")
    print("=" * 80)

    engine = get_live_trader_engine()
    engine.mode = "live"

    wallet = engine.wallet_address or os.getenv("POLY_FUNDER") or os.getenv("RELAYER_API_KEY_ADDRESS") or ""
    print(f"\n[1/5] Checking account credentials for wallet: {wallet}")
    acc = fetch_polymarket_account_value(wallet)
    print(f"      - Cash Collateral:  ${acc.get('cash_balance', 0.0):.2f}")
    print(f"      - Net Value:        ${acc.get('net_value', 0.0):.2f}")

    print(f"\n[2/5] Fetching upcoming window (T+1) for {args.series}...")
    pair = fetch_live_and_upcoming_markets(args.series)
    nxt = pair.get("next")
    if not nxt:
        print("[!] Error: Could not discover upcoming market (T+1).")
        return 1

    next_slug = nxt.get("slug", "")
    next_cid = nxt.get("conditionId", "")
    up_token = nxt.get("up_token", "")
    down_token = nxt.get("down_token", "")
    start_ts = float(nxt.get("start_ts", 0.0))
    time_until_start = max(0.0, start_ts - time.time())

    print(f"      - Market Slug:      {next_slug}")
    print(f"      - Market URL:       https://polymarket.com/event/{next_slug}")
    print(f"      - Condition ID:     {next_cid}")
    print(f"      - Starts In:        {int(time_until_start)}s")
    print(f"      - UP Token ID:      {up_token}")
    print(f"      - DOWN Token ID:    {down_token}")

    if args.dry_run:
        print("\n[DRY RUN] Would place 2 BUY limit orders on UPCOMING market:")
        print(f"          1. BUY UP   @ ${args.price:.2f} ({args.size} shares)")
        print(f"          2. BUY DOWN @ ${args.price:.2f} ({args.size} shares)")
        print("          Total Pair Cost: $%.2f" % (args.price * 2))
        print("[DRY RUN] Completed successfully.")
        return 0

    print(f"\n[3/5] Placing 2 REAL limit orders on Polymarket CLOB:")
    print(f"      - Order 1: BUY UP   @ ${args.price:.2f} ({args.size} shares)")
    print(f"      - Order 2: BUY DOWN @ ${args.price:.2f} ({args.size} shares)")

    res_up = engine.place_live_quote(token_id=up_token, price=args.price, size=args.size, side="BUY")
    res_dn = engine.place_live_quote(token_id=down_token, price=args.price, size=args.size, side="BUY")

    up_id = res_up.get("order_id") if res_up else None
    dn_id = res_dn.get("order_id") if res_dn else None

    print(f"     UP Order ID:   {up_id or 'FAILED'}")
    print(f"     DOWN Order ID: {dn_id or 'FAILED'}")
    if not up_id and not dn_id:
        print("\n[!] Both order placements failed; nothing to cancel.")
        return 1
    print("\n[OK] ORDERS PLACED.")
    print(f"\n[>>] CHECK YOUR POLYMARKET UI NOW (Open Orders tab)!")
    print(f"     URL: https://polymarket.com/event/{next_slug}")
    active_legs = [leg for leg, oid in (('UP', up_id), ('DOWN', dn_id)) if oid]
    print(f"     Resting order(s) active on leg(s): {', '.join(active_legs)} @ ${args.price:.2f}.")

    print(f"\n[4/5] Waiting {args.auto_cancel_sec} seconds before cancelling both orders...")
    try:
        for rem in range(args.auto_cancel_sec, 0, -1):
            print(f"      Cancelling in {rem}s... (Press Ctrl+C to cancel immediately)", end="\r", flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[!] Wait interrupted by operator (Ctrl+C). Proceeding to cancellation...")
    print("\n")

    print(f"[5/5] Cancelling orders on Polymarket CLOB...")
    client = engine.get_clob_client()
    ids_to_cancel = [oid for oid in (up_id, dn_id) if oid]
    if client and ids_to_cancel:
        try:
            client.cancel_orders(ids_to_cancel)
            print(f"[OK] CANCELLED BOTH ORDERS SUCCESSFULLY: {ids_to_cancel}")
        except Exception as e:
            print(f"[!] cancel_orders failed ({e}), invoking emergency cancel_all...")
            engine.cancel_all_orders()
            print("[OK] Emergency cancel_all completed.")
    else:
        engine.cancel_all_orders()

    print("\n" + "=" * 80)
    print(" [OK] UPCOMING PAIR PRE-QUOTING TEST COMPLETED")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
