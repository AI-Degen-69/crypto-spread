import json
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "research/sweeps/phase1_1d.json"
data = json.load(open(path, encoding="utf-8"))
rows = data if isinstance(data, list) else [data]
want = sys.argv[2] if len(sys.argv) > 2 else None

for r in rows:
    if want and r["name"] != want:
        continue
    print(f"=== {r['name']} (n={r['n']}) tot=${r['total_pnl_usd']:+.2f} "
          f"mean={r['mean_net_cents']:+.2f}c ci_lo={r['ci95_lo']:+.2f} "
          f"day_lo={r['ci95_day_lo'] if r['ci95_day_lo'] is not None else float('nan'):+.2f}")
    for grp in ("by_day", "by_series", "by_duration", "by_class"):
        print(f"--- {grp} ---")
        for k, v in r[grp].items():
            print(f"  {k:<26} n={v['n']:<5} pair={v['pair_rate']*100:>5.1f}% "
                  f"exit={v['exit_rate']*100:>5.1f}% win={v['win_rate']*100:>5.1f}% "
                  f"mean={v['mean_net_cents']:>+7.2f}c tot={v['total_usd']:>+8.2f}$ "
                  f"lo={v['ci95_lo_approx']:>+6.2f}c")
