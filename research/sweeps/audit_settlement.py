"""Settlement-bias audit for hold-to-settle (ex=none) configs.

For every window where exactly one leg filled and no exit fired, classify the
held leg by the last observed mid (won vs lost) and by whether the engine could
mark it (final bid present) or not (None -> engine books 0). Compare engine
marking vs true settlement (1/0) to bound the artifact.

Usage: python research/sweeps/audit_settlement.py
"""
from __future__ import annotations

import statistics
import sys
from dataclasses import asdict, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backtest.engine import BacktestParams, resolve_redemption
from ev_lab import _get_cache, fast_simulate, default_base_params, _mid_from


def main() -> int:
    cache = _get_cache()
    base = default_base_params()
    engine_mark_wins = engine_mark_losses = engine_mark_none = 0
    true_wins = true_losses = 0
    pnl_engine_list = []
    pnl_true_list = []
    pnl_fixed_list = []
    bias_windows = []

    for w in cache:
        p = replace(base, exit_thresh_by_slug={
            "default_5m": 0.49, "default_15m": 0.49,
            "btc-up-or-down-5m": 0.49, "sol-up-or-down-5m": 0.49,
            "btc-up-or-down-15m": 0.49, "sol-up-or-down-15m": 0.49})
        r = fast_simulate(w, p)
        if not ((r["filled_up"] != r["filled_dn"]) and not r["exit"] and not r["pair"]):
            continue
        held_up = r["filled_up"]
        # Recovering the resting price from the pnl components is unreliable,
        # so re-derive the anchor with *exactly* the rule `fast_simulate` uses
        # (issue #182). This previously diverged twice: it skipped the up-book
        # fallback and went straight to 0.50, and it always applied the up-leg
        # formula with a literal 0.02 even for a held down leg. Either one
        # assigns `true_pnl` an entry price the simulated trade never had —
        # in the audit whose whole job is to correct a ~40% optimism bias.
        init_mid = None
        for m in w.s_mid:
            if m is not None:
                init_mid = float(m)
                break
        if init_mid is None:
            for k in range(len(w.ts)):
                u_m = _mid_from(w.up_bb[k], w.up_ba[k])
                if u_m is not None:
                    init_mid = float(u_m)
                    break
        if init_mid is None:
            init_mid = 0.50
        anchor = init_mid if held_up else (1.0 - init_mid)
        resting = round(min(0.99, max(0.01, anchor - p.offset)), 3)
        last_bid = w.up_bb[-1] if held_up else w.dn_bb[-1]
        last_mid = w.s_mid[-1] if w.s_mid[-1] is not None else (
            (w.up_bb[-1] + w.up_ba[-1]) / 2.0 if held_up and w.up_bb[-1] is not None and w.up_ba[-1] is not None
            else (w.dn_bb[-1] + w.dn_ba[-1]) / 2.0 if (not held_up) and w.dn_bb[-1] is not None and w.dn_ba[-1] is not None
            else None)
        if last_mid is None:
            continue
        # true settlement: held leg wins if the window decided toward that side
        # (mid near 1 for up, near 0 for down). Use 0.5 on the last mid as proxy.
        won = (last_mid > 0.5) if held_up else (last_mid < 0.5)
        if won:
            true_wins += 1
            true_pnl = (1.0 - resting) * 100.0
        else:
            true_losses += 1
            true_pnl = (0.0 - resting) * 100.0
        pnl_true_list.append(true_pnl)
        if last_bid is None:
            engine_mark_none += 1
            engine_pnl = 0.0
            bias_windows.append((w.cid, w.day, w.series, held_up, resting, true_pnl, won))
            # What the shared resolver books for the same window (issue #191).
            # `fast_simulate` deliberately keeps recording the raw zero and
            # leaves the correction to `summarize(settle_correct=True)`, so the
            # fixed figure is reconstructed here the same way the summarizer
            # reconstructs it -- through the one definition in
            # `backtest.engine`, never a local copy of the rule.
            _won, delta = resolve_redemption(
                {"best_bid": w.up_bb[-1], "best_ask": w.up_ba[-1]},
                {"best_bid": w.dn_bb[-1], "best_ask": w.dn_ba[-1]},
                held_up, resting)
            fixed_pnl = delta if _won is not None else 0.0
        else:
            engine_pnl = (last_bid - resting) * 100.0
            fixed_pnl = engine_pnl
            if engine_pnl > 0:
                engine_mark_wins += 1
            else:
                engine_mark_losses += 1
        pnl_engine_list.append(engine_pnl)
        pnl_fixed_list.append(fixed_pnl)

    n = len(pnl_engine_list)
    # `pnl_*_list` holds cents for ONE contract. Dividing by 100 gives the
    # one-contract dollar total, which the output then labelled "at size 5" —
    # understating every total fivefold (issue #182). Scale by the same
    # `quote_shares` the simulation used rather than re-labelling, since the
    # figure is quoted against the size the strategy actually trades.
    size = base.quote_shares
    to_usd = size / 100.0
    print(f"naked-settle windows: {n}")
    print(f"engine mark: wins={engine_mark_wins} losses={engine_mark_losses} "
          f"none-marked(0 pnl)={engine_mark_none}")
    print(f"true settlement: wins={true_wins} losses={true_losses}")
    print(f"engine mean pnl/naked window: {statistics.fmean(pnl_engine_list):+.2f}c "
          f"(total {sum(pnl_engine_list)*to_usd:+.2f}$ at size {size})")
    print(f"true   mean pnl/naked window: {statistics.fmean(pnl_true_list):+.2f}c "
          f"(total {sum(pnl_true_list)*to_usd:+.2f}$ at size {size})")
    print(f"bias (true - engine) total: "
          f"{(sum(pnl_true_list)-sum(pnl_engine_list))*to_usd:+.2f}$ at size {size}")
    # The raw line above is the evidence in issue #191 and stays as it is. The
    # two below measure the same windows through the settlement resolver, and
    # are what should now land at roughly zero.
    print(f"fixed  mean pnl/naked window: {statistics.fmean(pnl_fixed_list):+.2f}c "
          f"(total {sum(pnl_fixed_list)*to_usd:+.2f}$ at size {size})")
    print(f"bias (true - fixed)  total: "
          f"{(sum(pnl_true_list)-sum(pnl_fixed_list))*to_usd:+.2f}$ at size {size}")
    if bias_windows:
        tot = sum(b[5] for b in bias_windows)
        print(f"unmarked windows: {len(bias_windows)}, "
              f"their true total pnl: {tot*to_usd:+.2f}$ at size {size}")
        from collections import Counter
        print("unmarked by won/lost:",
              Counter("won" if b[6] else "lost" for b in bias_windows))
        print("unmarked by day:", Counter(b[1] for b in bias_windows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
