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
    max_skew: float = 0.015
    min_reward_offset: float = 0.005

    per_market_risk_pct: float = 0.05
    fleet_risk_pct: float = 0.20
    max_naked_usd_abs: float = 25.0
    max_fleet_naked_usd_abs: float = 100.0
    max_naked_usd: float = 120.0
    enable_hard_blocks: bool = True
    float_mark_retention_days: int = 7

    decided_price: float = 0.02
    max_book_spread: float = 0.06
    min_book_depth_sh: float = 200.0

    emergency_hedge_frac: float = 0.50
    enable_emergency_hedge: bool = False

    markout_horizons: tuple[int, ...] = (30, 60, 300)
    markout_min_sample: int = 5
    markout_fleet_min_sample: int = 15
    markout_widen_threshold: float = -0.010
    markout_catastrophic_threshold: float = -0.025
    widen_offset: float = 0.005

    marginal_return_floor: float = 0.05
    allocation_budget: float = 90.0
    max_market_frac: float = 0.25
    gate_state: str = "open"

    profit_take_fee_per_share: float = 0.002
    merge_gas_usd: float = 0.05
    merge_max_loss_per_share: float = 0.005
    merge_velocity_hold_days: int = 3

    reward_min_payout_usd: float = 1.0
    reward_floor_multiple: float = 2.0
    spread_capture_frac: float = 0.50
    spread_capture_default_spread: float = 0.04

    select_min_volume_24h_usd: float = 500.0
    select_min_top3_depth_usd: float = 300.0
    select_min_top3_depth_usd_trial: float = 0.0
    select_min_volume_24h_usd_trial: float = 0.0
    select_max_book_spread: float = 0.06
    select_max_days_to_resolve: float = 3.0

    rank_sample_window_sec: int = 3600
    rerank_interval_sec: int = 300

    profit_take_net_threshold: float = 0.015
    scarcity_close_threshold: float = 0.005
    scarcity_marginal_multiple: float = 1.5
    capital_scarce: bool = False

    max_fleet_naked_usd: float = 400.0
    fleet_naked_usd: float = 0.0
    max_committed_usd: float = 1000.0
    committed_usd: float = 0.0
    fleet_posture: str = "normal"

    est_reward_pool_usd: float = 143.0
    quote_both_sides: bool = True
    ticks_below_ask: int = 1
    tick_size: float = 0.01
    price_tick: float = 0.01
    min_quote_shares: int = 50
    max_spread_from_mid: float = 0.045
    max_rest_queue_ahead: int = 500
    quote_shares: int = 120

    price_band_low: float = 0.10
    price_band_high: float = 0.90
    quote_window_frac: float = 0.05
    enforce_price_band: bool = True
    enforce_quote_window: bool = False

    coinflip_halfwidth: float = 0.05
    coinflip_size_cut: float = 0.50
    price_risk_widen: float = 0.010
    target_balance: float = 0.50

    # NOTE: Historical hunter-fleet threshold. Engine default in LiveTraderEngine is 0.99.
    max_pair_cost: float = 0.995

    max_fills_per_market: int = 25
    requote_interval_sec: float = 2.0
    poll_interval_sec: float = 1.0
    min_t_remaining_sec: float = 15.0
    max_cost_per_market: float = 200.0
    max_open_markets: int = 4

    enable_pairs_rule: bool = True
    enable_leg_chase: bool = True
    pairs_exit_window_sec: int = 900
    pairs_complete_gain_cents: float = 3.68
    pairs_exit_cost_cents: float = 3.67

    experiment_census_markets: int = 10
    experiment_verdict_markets: int = 20
    hedge_fillable_min_rate: float = 0.70
    balance_hedge_sec: int = 60

    maker_fee: float = 0.0
    rebate_rate: float = 0.20
    fee_rate: float = 0.02
    net_oneway_ms: float = 3.93
    cancel_venue_ack_ms: float = 25.0
    post_venue_accept_ms: float = 81.0

    sim_only: bool = False
    pinned_condition_id: str = ""
    market_title: str = ""
    market_url: str = ""
    market_daily_rate: float = 0.0
