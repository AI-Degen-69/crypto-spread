"""Stream live spot prices from Polymarket RTDS."""
import argparse
import logging
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
from strategy.streaming import UnifiedStreamBridge


def main() -> None:
    """Run live crypto spot ticker stream."""
    parser = argparse.ArgumentParser(description="Live crypto spot stream from Polymarket RTDS")
    parser.add_argument("--symbols", nargs="+", default=["btcusdt"], help="Symbols to track")
    parser.add_argument("--seconds", type=int, default=0, help="Run for N seconds then exit (0 = run forever)")
    args = parser.parse_args()

    if args.seconds < 0:
        parser.error("--seconds must be non-negative (0 = run forever)")

    symbols = [s.lower() for s in args.symbols]
    print(f"Connecting to live RTDS stream for: {', '.join(symbols)}...", flush=True)
    print("Press Ctrl+C to stop.\n", flush=True)

    def on_tick(sym: str, ts: int, px: float) -> None:
        """Handle and display incoming spot tick."""
        t_str = time.strftime("%H:%M:%S", time.localtime(ts / 1000.0))
        print(f"[{t_str}] ⚡ {sym.upper():<7} = ${px:,.2f}", flush=True)

    bridge = UnifiedStreamBridge(symbols=symbols, on_spot_tick=on_tick)
    bridge.start()

    start_time = time.time()
    try:
        while True:
            time.sleep(0.5)
            if not bridge.is_running or not bridge.is_rtds_running:
                print("\nStream worker ended unexpectedly.", flush=True)
                break
            if args.seconds > 0 and (time.time() - start_time) >= args.seconds:
                break
    except KeyboardInterrupt:
        print("\nStopping stream...", flush=True)
    finally:
        bridge.stop()
        print("Done.", flush=True)


if __name__ == "__main__":
    main()
