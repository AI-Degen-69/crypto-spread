"""RETIRED / ARCHIVAL configuration module (Issue #426, ADR-0004).

DO NOT USE FOR RUNTIME CONFIGURATION.

Neither the live trading engine nor the backtest engine constructs or consumes
this module. Configuration is owned exclusively by:
- Live / Paper Trading: `LiveTraderEngine.__init__` in `strategy/live_trader.py`
- Backtesting / Replay: `BacktestParams` in `backtest/engine.py`

Detailed measured rationale and historical research notes from the July 2026
hunter-fleet experiment are preserved in `docs/maker-config-legacy-rationale.md`.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MakerConfig:
    """Historical market-maker bot hyperparameters (RETIRED / NON-RUNTIME).

    This dataclass is retained strictly as an archival schema and reference
    for historical documentation citations. It is NOT instantiated or read by
    `LiveTraderEngine` or `BacktestParams`.

    WARNING: Do NOT cite defaults in this class as live engine defaults.
    In particular, `max_pair_cost = 0.995` below was a historical hunter-fleet
    threshold; the canonical trading engine default in `LiveTraderEngine` is `0.99`.
    """

    series_slug: str = "btc-up-or-down-5m"
    bankroll_usd: float = 100.0

    # Objective: historical options were "pair" vs "rewards"
    objective: str = "rewards"
    reward_offset: float = 0.020
    max_skew: float = 0.015             # cap, in price units
    min_reward_offset: float = 0.005    # never quote nearer mid than this

    per_market_risk_pct: float = 0.01
    fleet_risk_pct: float = 0.066
    max_naked_usd_abs: float = 120.0
    max_fleet_naked_usd_abs: float = 400.0
    max_naked_usd: float = 120.0
    enable_hard_blocks: bool = True
    float_mark_retention_days: float = 90.0

    decided_price: float = 0.02
    max_book_spread: float = 0.06
    min_book_depth_sh: float = 200.0

    emergency_hedge_frac: float = 0.8
    enable_emergency_hedge: bool = True

    markout_horizons: tuple[float, ...] = (300.0, 3600.0, 21600.0, 900.0)
    markout_min_sample: int = 8
    markout_fleet_min_sample: int = 25
    markout_widen_threshold: float = -0.005
    markout_catastrophic_threshold: float = -0.020
    widen_offset: float = 0.035

    marginal_return_floor: float = 0.02
    allocation_budget: float = 900.0
    max_market_frac: float = 0.15
    gate_state: str = "NORMAL"

    profit_take_fee_per_share: float = 0.017
    merge_gas_usd: float = 0.0
    merge_max_loss_per_share: float = 0.01
    merge_velocity_hold_days: float = 30.0

    reward_min_payout_usd: float = 1.0
    reward_floor_multiple: float = 1.5
    spread_capture_frac: float = 0.25
    spread_capture_default_spread: float = 0.01

    select_min_volume_24h_usd: float = 250_000.0
    select_min_top3_depth_usd: float = 1_000.0
    select_min_top3_depth_usd_trial: float | None = None
    select_min_volume_24h_usd_trial: float | None = None
    select_max_book_spread: float = 0.06
    select_max_days_to_resolve: float = 30.0

    rank_sample_window_sec: float = 1800.0
    rerank_interval_sec: float = 600.0

    profit_take_net_threshold: float = 0.020
    scarcity_close_threshold: float = -0.005
    scarcity_marginal_multiple: float = 2.0
    capital_scarce: bool = False

    max_fleet_naked_usd: float = 400.0
    fleet_naked_usd: float = 0.0
    max_committed_usd: float = 1000.0
    committed_usd: float = 0.0
    fleet_posture: str = "NORMAL"

    est_reward_pool_usd: float = 143.0
    quote_both_sides: bool = True
    ticks_below_ask: int = 1
    tick_size: float = 0.01
    price_tick: float = 0.001
    min_quote_shares: int = 50
    max_spread_from_mid: float = 0.045
    max_rest_queue_ahead: float = 50.0
    quote_shares: int = 120

    price_band_low: float = 0.10
    price_band_high: float = 0.90
    quote_window_frac: float = 0.40
    enforce_price_band: bool = True
    enforce_quote_window: bool = True

    coinflip_halfwidth: float = 0.20
    coinflip_size_cut: float = 0.55
    price_risk_widen: float = 0.010
    target_balance: float = 0.92

    # NOTE: Historical hunter-fleet threshold. Engine default in LiveTraderEngine is 0.99.
    max_pair_cost: float = 0.995

    max_fills_per_market: int = 25
    requote_interval_sec: float = 2.0
    poll_interval_sec: float = 1.0
    min_t_remaining_sec: float = 15.0
    max_cost_per_market: float = 400.0
    max_open_markets: int = 3

    enable_pairs_rule: bool = True
    enable_leg_chase: bool = True
    pairs_exit_window_sec: float = 900.0
    pairs_complete_gain_cents: float = 3.68
    pairs_exit_cost_cents: float = 3.67

    experiment_census_markets: int = 60
    experiment_verdict_markets: int = 120
    hedge_fillable_min_rate: float = 0.50
    balance_hedge_sec: float = 20.0

    maker_fee: float = 0.0
    rebate_rate: float = 0.20
    fee_rate: float = 0.07          # taker fee rate, for the rebate estimate

    net_oneway_ms: float = 3.93
    cancel_venue_ack_ms: float = 150.0     # tau_cancel = 153.93ms total (estimate)
    post_venue_accept_ms: float = 81.0     # tau_post   = 84.93ms total (~1.8x lower than 150ms estimate) [MEASURED]

    sim_only: bool = True
    pinned_condition_id: str = ""
    market_title: str = ""
    market_url: str = ""
    market_daily_rate: float = 0.0
