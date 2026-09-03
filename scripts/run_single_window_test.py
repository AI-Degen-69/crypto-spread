"""Single Window Live Experiment: Advance Pre-Quoting + Fill Tracking + Exit / Merge.

Places 2 real orders (UP @ 0.48, DOWN @ 0.48) on the upcoming 5m BTC window,
tracks the entire lifecycle through the 5-minute duration, and executes Stop Loss or Merge.
"""
import argparse
import datetime
import os
import sys
import time

from strategy.live_trader import (
    get_live_trader_engine,
    fetch_live_and_upcoming_markets,
    fetch_polymarket_account_value,
    _load_env_file,
    CLOB_HOST,
)
from strategy.series import filter_series
from strategy.markets import full_book


def main():
    """Execute live single window experiment with advance pre-quoting."""
    parser = argparse.ArgumentParser(description="Live Window Advance Pre-Quoting Session")
    parser.add_argument("--series", default=None, help="Direct series slug (e.g. btc-up-or-down-5m)")
    parser.add_argument("--tokens", default=None, help="Comma-separated tokens (e.g. BTC, ETH)")
    parser.add_argument("--duration", choices=["5m", "15m", "both"], default=None, help="Window duration (5m, 15m, both)")
    parser.add_argument("--price-offset", type=float, default=0.02, help="Offset from 0.50 (default: 0.02 -> 0.48/0.48)")
    parser.add_argument("--shares", type=float, default=5.0, help="Order shares (default: 5.0)")
    parser.add_argument("--exit-thresh", type=float, default=0.05, help="Stop loss drift threshold (default: 0.05)")
    args = parser.parse_args()

    # Resolve target single series from arguments
    target_series = "btc-up-or-down-5m"
    if args.series:
        target_series = args.series
    elif args.tokens or args.duration:
        toks = [t.strip() for t in args.tokens.split(",") if t.strip()] if args.tokens else None
        durs = None
        if args.duration == "5m":
            durs = [300]
        elif args.duration == "15m":
            durs = [900]
        elif args.duration == "both":
            durs = [300, 900]
        resolved = filter_series(tokens=toks, durations=durs)
        if not resolved:
            print("[!] Error: No series matched tokens/duration selection.")
            return 1
        if len(resolved) > 1:
            print(f"[!] Error: Single window test requires a single market; selection resolved to {len(resolved)}: {[s[0] for s in resolved]}. Please narrow to one token and duration (e.g. --tokens BTC --duration 15m).")
            return 1
        target_series = resolved[0][0]

    _load_env_file()
    print("=" * 80)
    print(f" [*] LIVE SPREAD-2 WINDOW EXPERIMENT ({target_series})")
    print("=" * 80)

    engine = get_live_trader_engine()
    engine.mode = "live"

    wallet = engine.wallet_address or os.getenv("POLY_FUNDER") or os.getenv("RELAYER_API_KEY_ADDRESS") or ""
    print(f"\n[1/5] Checking account credentials for wallet: {wallet}")
    acc = fetch_polymarket_account_value(wallet)
    print(f"      - Cash Collateral:  ${acc.get('cash_balance', 0.0):.2f}")
    print(f"      - Net Value:        ${acc.get('net_value', 0.0):.2f}")

    print(f"\n[2/5] Discovering upcoming window (T+1) for {target_series}...")
    pair = fetch_live_and_upcoming_markets(target_series)
    nxt = pair.get("next")
    if not nxt:
        print("[!] Error: Upcoming market not available yet.")
        return 1

    next_slug = nxt.get("slug", "")
    next_cid = nxt.get("conditionId", "")
    up_token = nxt.get("up_token", "")
    down_token = nxt.get("down_token", "")
    start_ts = float(nxt.get("start_ts", 0.0))
    end_ts = float(nxt.get("end_ts", 0.0))

    quote_price = round(0.50 - args.price_offset, 2)
    pair_cost = quote_price * 2.0

    print(f"      - Market Slug:      {next_slug}")
    print(f"      - Direct URL:       https://polymarket.com/event/{next_slug}")
    print(f"      - Condition ID:     {next_cid}")
    print(f"      - Quoting Price:    ${quote_price:.2f} on BOTH legs (Pair: ${pair_cost:.2f})")
    print(f"      - Size per leg:     {args.shares} shares (${quote_price * args.shares:.2f})")
    print(f"      - Starts In:        {int(max(0, start_ts - time.time()))}s")

    print(f"\n[3/5] Sending ADVANCE PRE-QUOTES to Polymarket CLOB:")
    res_up = engine.place_live_quote(token_id=up_token, price=quote_price, size=args.shares, side="BUY")
    res_dn = engine.place_live_quote(token_id=down_token, price=quote_price, size=args.shares, side="BUY")

    up_id = res_up.get("order_id") if res_up else None
    dn_id = res_dn.get("order_id") if res_dn else None
    for leg, res in (("UP", res_up), ("DOWN", res_dn)):
        if isinstance(res, dict) and res.get("error"):
            print(f"      - {leg} placement error: {res['error']}")

    print(f"      - UP Order ID:   {up_id or 'FAILED'}")
    print(f"      - DOWN Order ID: {dn_id or 'FAILED'}")

    if not up_id and not dn_id:
        print("[!] Both order placements failed.")
        return 1

    print(f"\n[>>] ORDERS ARE NOW RESTING ON POLYMARKET CLOB!")
    print(f"     Open your browser to: https://polymarket.com/event/{next_slug}")
    print(f"     Check the Open Orders tab.")

    print(f"\n[4/5] Entering Live Monitoring Loop (Ctrl+C to safely exit & cancel orders)...")

    filled_up = False
    filled_down = False
    pair_captured = False
    exit_taken = False
    max_up_drift = 0.0
    max_down_drift = 0.0

    clob_client = engine.get_clob_client()
    if not clob_client:
        print("[!] Error: CLOB client not configured; cannot track fills.")
        return 1

    try:
        while True:
            now = time.time()
            rem = end_ts - now
            until_start = start_ts - now

            if until_start > 0:
                print(f"      [PRE-WINDOW] Market starts in {int(until_start)}s... Resting orders active.       ", end="\r", flush=True)
                time.sleep(1.5)
                continue

            if rem <= 15:
                print(f"\n[!] Window ending in {int(rem)}s — Cancelling unfilled resting orders for safety...")
                break

            # Poll books
            ubook = full_book(CLOB_HOST, up_token)
            dbook = full_book(CLOB_HOST, down_token)

            up_bid = ubook.get("best_bid")
            up_ask = ubook.get("best_ask")
            dn_bid = dbook.get("best_bid")
            dn_ask = dbook.get("best_ask")

            mid = 0.50
            if up_bid is not None and up_ask is not None:
                mid = (up_bid + up_ask) / 2.0

            if mid > 0.50:
                max_up_drift = max(max_up_drift, mid - 0.50)
            else:
                max_down_drift = max(max_down_drift, 0.50 - mid)

            # Check Fills directly on Polymarket CLOB
            if not filled_up and up_id:
                try:
                    ord_up = clob_client.get_order(up_id)
                    st_up = (ord_up.get("status") or "").upper()
                    sz_up = float(ord_up.get("size_matched", 0.0) or 0.0)
                    if st_up in ("MATCHED", "FILLED") or sz_up >= args.shares:
                        filled_up = True
                        print(f"\n[!] [FILL CONFIRMED ON CLOB] UP leg FILLED @ ${quote_price:.2f} ({args.shares} shares, status={st_up})!")
                except Exception as e:
                    print(f"\n[!] UP order status poll failed: {e}")

            if not filled_down and dn_id:
                try:
                    ord_dn = clob_client.get_order(dn_id)
                    st_dn = (ord_dn.get("status") or "").upper()
                    sz_dn = float(ord_dn.get("size_matched", 0.0) or 0.0)
                    if st_dn in ("MATCHED", "FILLED") or sz_dn >= args.shares:
                        filled_down = True
                        print(f"\n[!] [FILL CONFIRMED ON CLOB] DOWN leg FILLED @ ${quote_price:.2f} ({args.shares} shares, status={st_dn})!")
                except Exception as e:
                    print(f"\n[!] DOWN order status poll failed: {e}")

            # 1. Both Filled -> Pair Capture & Merge!
            if filled_up and filled_down and not pair_captured:
                pair_captured = True
                pair_profit = (1.00 - pair_cost) * args.shares
                print(f"\n{'*' * 80}")
                print(f" [★] PAIR CAPTURED! Both legs filled @ ${quote_price:.2f} + ${quote_price:.2f} = ${pair_cost:.2f}")
                print(f"     Gross Profit: +${pair_profit:.2f} (+{((1.00 - pair_cost)/pair_cost)*100:.1f}%)")
                print(f"     Ready for CTF mergePositions! Position locked in profit.")
                print(f"{'*' * 80}\n")
                break

            # 2. Stop Loss Exit Check (if only 1 leg filled and market drifted adversely)
            cur_down_drift = max(0.0, 0.50 - mid)
            if filled_up and not filled_down and cur_down_drift >= args.exit_thresh and not exit_taken:
                exit_taken = True
                print(f"\n[!] [STOP LOSS] Market dropped adversely (drift: {cur_down_drift:.3f} >= {args.exit_thresh:.2f}, max seen: {max_down_drift:.3f})!")
                print(f"    Cancelling unhedged DOWN order...")
                if dn_id:
                    engine.cancel_live_order(dn_id)
                if up_bid is not None:
                    print(f"    Selling UP shares @ best bid ${up_bid:.2f} to prevent full loss...")
                    engine.place_live_quote(token_id=up_token, price=up_bid, size=args.shares, side="SELL")
                    loss = (up_bid - quote_price) * args.shares
                    print(f"    [EXIT COMPLETE] Realized Loss: ${loss:.2f} (protected from 100% loss).")
                break

            cur_up_drift = max(0.0, mid - 0.50)
            if filled_down and not filled_up and cur_up_drift >= args.exit_thresh and not exit_taken:
                exit_taken = True
                print(f"\n[!] [STOP LOSS] Market rallied adversely (drift: {cur_up_drift:.3f} >= {args.exit_thresh:.2f}, max seen: {max_up_drift:.3f})!")
                print(f"    Cancelling unhedged UP order...")
                if up_id:
                    engine.cancel_live_order(up_id)
                if dn_bid is not None:
                    print(f"    Selling DOWN shares @ best bid ${dn_bid:.2f} to prevent full loss...")
                    engine.place_live_quote(token_id=down_token, price=dn_bid, size=args.shares, side="SELL")
                    loss = (dn_bid - quote_price) * args.shares
                    print(f"    [EXIT COMPLETE] Realized Loss: ${loss:.2f} (protected from 100% loss).")
                break

            t_str = datetime.datetime.now().strftime("%H:%M:%S")
            print(f"[{t_str}] Rem: {int(rem)}s | Mid: {mid:.3f} | UP Book: {up_bid}/{up_ask} | DN Book: {dn_bid}/{dn_ask} | UP: {'FILLED' if filled_up else 'RESTING'} | DN: {'FILLED' if filled_down else 'RESTING'}   ", end="\r", flush=True)
            time.sleep(1.5)

    except KeyboardInterrupt:
        print("\n[!] User interrupted session.")

    finally:
        print(f"\n[5/5] Cleanup: ensuring no unfilled resting orders remain...")
        client = engine.get_clob_client()
        ids = []
        if not filled_up and up_id:
            ids.append(up_id)
        if not filled_down and dn_id:
            ids.append(dn_id)

        if ids and client:
            try:
                client.cancel_orders(ids)
                print(f"[OK] Cancelled remaining resting orders: {ids}")
            except Exception as e:
                print(f"[!] cancel_orders error ({e}), invoking cancel_all...")
                engine.cancel_all_orders()
        else:
            print("[OK] No resting orders needed cancellation.")

    print("\n" + "=" * 80)
    print(" [OK] WINDOW EXPERIMENT COMPLETED")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
