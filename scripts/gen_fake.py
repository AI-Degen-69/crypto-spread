"""Generate fake ticks with round numbers for backtest mechanics verification.

3 windows, all BTC 5m, 300s duration, known expected results:
- Window A (oscillating): mid goes 0.50 -> 0.48 (down) -> 0.52 (up) -> both sides fill -> pair -> +4c
- Window B (monotonic): mid 0.50 -> 0.60 (up 10c) -> only DOWN fills? Actually UP book mid 0.60, resting DOWN = 1-0.60-0.02=0.38, tape at 0.38 fills DOWN, UP not filled, then exit -> loss
- Window C (flat): mid 0.50 -> 0.51 (1c) -> no fill -> 0

All numbers round to 2 decimals, tape at exact resting prices.
"""
import json, pathlib
from pathlib import Path

OUT = Path(r"C:\Users\Tiger\Agents\Projects\AI Trading\crypto-spread\run\ticks\fake_round.jsonl")
OUT.parent.mkdir(parents=True, exist_ok=True)

SERIES = "btc-up-or-down-5m"
DUR = 300
UP_TOK = "0xUP_FAKE"
DN_TOK = "0xDN_FAKE"

def mk(ts, cid, slug, mid, tape=None, up_ask=None, down_ask=None, start_ts=None, end_ts=None):
    bb_up = round(mid - 0.005, 4)
    ba_up = round(mid + 0.005, 4)
    # Keep touch ~0.99 so pair_cost 0.995 passes even at mid 0.50
    # Set down asks to make touch 0.99 regardless of mid
    ba_dn = round(0.99 - ba_up, 4)
    bb_dn = round(ba_dn - 0.005, 4)
    return {
        "ts": ts, "iso": "2026-08-31T00:00:00+00:00", "series": SERIES, "duration": DUR,
        "label": "BTC 5m", "cid": cid, "slug": slug,
        "start_ts": start_ts or (ts - 10), "end_ts": end_ts or (ts + 290), "t_rem": 290,
        "up_token": UP_TOK, "down_token": DN_TOK,
        "up_book": {"token_id": UP_TOK, "bids": {"0.48": 10}, "asks": {}, "best_bid": bb_up, "best_ask": ba_up, "malformed": 0},
        "down_book": {"token_id": DN_TOK, "bids": {"0.48": 10}, "asks": {}, "best_bid": bb_dn, "best_ask": ba_dn, "malformed": 0},
        "tape_delta": tape or [],
        "mid": mid, "touch_pair": (ba_up or 0.5)+(ba_dn or 0.5), "resting_pair": 0.96,
        "queue_up": 10, "queue_down": 10, "err": None,
    }

lines = []
# Window A: cid A, 4 ticks, oscillating, both sides fill at resting 0.48
cidA = "0xAAAA"
slugA = "btc-updown-5m-1000"
base = 1000.0
# tick1: mid 0.50, tape UP at 0.48 fills UP
lines.append(mk(base+0, cidA, slugA, 0.50, tape=[{"asset": UP_TOK, "price": 0.48, "size": 100}], start_ts=base, end_ts=base+300))
# tick2: mid 0.48, queue still 10, no tape
lines.append(mk(base+1, cidA, slugA, 0.48))
# tick3: mid 0.52, tape DOWN at 0.48 fills DOWN (resting_down = 1-0.52-0.02=0.46, but we set tape 0.46? Use 0.46)
# For mid 0.52, resting_down = 0.46, so tape at 0.46
lines[-1]["tape_delta"] = []  # clear previous
lines.append(mk(base+2, cidA, slugA, 0.52, tape=[{"asset": DN_TOK, "price": 0.46, "size": 100}]))
# tick4: mid 0.50 close
lines.append(mk(base+3, cidA, slugA, 0.50))

# Window B: cid B, monotonic up, only DOWN fills, then exit
cidB = "0xBBBB"
slugB = "btc-updown-5m-2000"
base2 = 2000.0
# tick1: mid 0.50, no fill
lines.append(mk(base2+0, cidB, slugB, 0.50, start_ts=base2, end_ts=base2+300))
# tick2: mid 0.55, tape DOWN at 0.43 (resting_down=1-0.55-0.02=0.43) fills DOWN
lines.append(mk(base2+1, cidB, slugB, 0.55, tape=[{"asset": DN_TOK, "price": 0.43, "size": 100}]))
# tick3: mid 0.37 (down 13c, past exit 9c for DOWN side), should trigger exit (max_down 0.13)
lines.append(mk(base2+2, cidB, slugB, 0.37))
# tick4: mid 0.36 close
lines.append(mk(base2+3, cidB, slugB, 0.36))

# Window C: cid C, flat, no fill
cidC = "0xCCCC"
slugC = "btc-updown-5m-3000"
base3 = 3000.0
lines.append(mk(base3+0, cidC, slugC, 0.50, start_ts=base3, end_ts=base3+300))
lines.append(mk(base3+1, cidC, slugC, 0.51))
lines.append(mk(base3+2, cidC, slugC, 0.50))

# Window D: cid D, monotonic with single fill no exit (max_up 0.08, no exit)
cidD = "0xDDDD"
slugD = "btc-updown-5m-4000"
base4 = 4000.0
# tick1: mid 0.50, no fill
lines.append(mk(base4+0, cidD, slugD, 0.50, start_ts=base4, end_ts=base4+300))
# tick2: mid 0.58 (up 8c), tape DOWN at 0.40 (1-0.58-0.02=0.40) fills DOWN
lines.append(mk(base4+1, cidD, slugD, 0.58, tape=[{"asset": DN_TOK, "price": 0.40, "size": 100}]))
# tick3: mid 0.57 close (max_up 0.08 <0.09, so no exit, stays naked)
lines.append(mk(base4+2, cidD, slugD, 0.57))

OUT.write_text("\n".join(json.dumps(l) for l in lines) + "\n", encoding="utf-8")
print(f"Wrote {len(lines)} ticks to {OUT}")
print(f"Windows: A={cidA} (4 ticks, oscillating, pair), B={cidB} (4 ticks, oscillating, exit), C={cidC} (3 ticks, flat), D={cidD} (3 ticks, monotonic, single fill)")
print("Expected with offset=0.02, queue=50, tape:")
print("  A: pair -> +3.96c")
print("  B: exit -> ~-36c")
print("  C: flat -> 0")
print("  D: monotonic single fill, no exit -> -57c settlement")
