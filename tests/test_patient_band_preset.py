"""Unit tests for Issue #137: patient undecided-band maker preset.

Covers: engine knobs (entry_delay_sec, entry_band, stop_loss_enabled),
entry-delay gate, post-delay entry-band gate + telemetry, stop-loss-off
semantics, and the patient_band_maker preset incl. API wiring.
"""

from unittest.mock import MagicMock

from strategy.live_trader import LiveTraderEngine


def _poll_data(start_ts: float, duration: float = 300.0, mid: float = 0.50,
               down_book: dict | None = None) -> dict:
    """Two-sided books centered so the synthetic mid equals `mid`.

    UP centers on `mid`, DOWN on `1 - mid`, mirroring a binary market, so
    mid = (up_mid + (1 - down_mid)) / 2 == `mid`.
    """
    half = 0.01
    return {
        "market": {
            "conditionId": "0x137",
            "slug": "mkt-137",
            "up_token": "tok_up_137",
            "down_token": "tok_dn_137",
            "start_ts": start_ts,
            "end_ts": start_ts + duration,
        },
        "up_book": {"best_bid": round(mid - half, 4), "best_ask": round(mid + half, 4)},
        "down_book": down_book if down_book is not None else {
            "best_bid": round((1.0 - mid) - half, 4),
            "best_ask": round((1.0 - mid) + half, 4),
        },
    }


def _live_engine(**config) -> LiveTraderEngine:
    engine = LiveTraderEngine()
    if config:
        # Applied before is_running flips, per the stop-the-bot-first contract.
        engine.update_config(**config)
    engine.mode = "live"
    engine.is_running = True
    engine.get_clob_client = MagicMock(return_value=None)
    engine.place_live_quote = MagicMock(
        side_effect=lambda tok, px, sz, side: {"order_id": f"ord_{tok}", "status": "RESTING"}
    )
    engine.cancel_live_order = MagicMock(return_value=True)
    return engine


def _paper_engine(**config) -> LiveTraderEngine:
    """Paper-mode engine (fills simulated from book asks)."""
    engine = LiveTraderEngine()
    if config:
        engine.update_config(**config)
    engine.is_running = True
    return engine


def _side_books(start_ts: float, up_bid: float, up_ask: float,
                dn_bid: float, dn_ask: float, duration: float = 300.0) -> dict:
    return {
        "market": {
            "conditionId": "0x137",
            "slug": "mkt-137",
            "up_token": "tok_up_137",
            "down_token": "tok_dn_137",
            "start_ts": start_ts,
            "end_ts": start_ts + duration,
        },
        "up_book": {"best_bid": up_bid, "best_ask": up_ask},
        "down_book": {"best_bid": dn_bid, "best_ask": dn_ask},
    }


# ============================================================================
# TASK 1: engine knobs — init + update_config + state echo
# ============================================================================

def test_issue137_knob_defaults_preserve_current_behavior():
    """New knobs default to off/True so default engine behavior is identical."""
    engine = LiveTraderEngine()
    assert engine.entry_delay_sec == 0.0
    assert engine.entry_band == 0.0
    assert engine.stop_loss_enabled is True
    assert engine.active_preset is None


def test_issue137_state_echoes_new_knobs():
    """get_state params echo the new knobs plus the active preset."""
    engine = LiveTraderEngine()
    params = engine.get_state()["params"]
    assert params["entry_delay_sec"] == 0.0
    assert params["entry_band"] == 0.0
    assert params["stop_loss_enabled"] is True
    assert engine.get_state()["active_preset"] is None


def test_issue137_update_config_sets_new_knobs():
    """update_config accepts and clamps the new knobs."""
    engine = LiveTraderEngine()
    engine.update_config(entry_delay_sec=60.0, entry_band=0.04, stop_loss_enabled=False)
    assert engine.entry_delay_sec == 60.0
    assert engine.entry_band == 0.04
    assert engine.stop_loss_enabled is False
    params = engine.get_state()["params"]
    assert params["entry_delay_sec"] == 60.0
    assert params["entry_band"] == 0.04
    assert params["stop_loss_enabled"] is False


def test_issue137_update_config_rejects_unknown_preset_atomically():
    """Unknown preset raises and leaves the whole configuration untouched."""
    engine = LiveTraderEngine()
    before = engine.get_state()["params"]
    try:
        engine.update_config(preset="no_such_preset")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unknown preset")
    after = engine.get_state()["params"]
    assert after == before
    assert engine.active_preset is None


# ============================================================================
# TASK 2: entry-delay gate
# ============================================================================

def test_issue137_delay_suppresses_quoting_before_expiry():
    """With entry_delay_sec=60, a tick 5s in places no orders and latches nothing."""
    engine = _live_engine(entry_delay_sec=60.0)
    slug = "btc-up-or-down-5m"
    engine._update_market_strategy(slug, _poll_data(1000.0), now=1005.0)
    mstate = engine.markets[slug]
    assert engine.place_live_quote.call_count == 0
    assert mstate.order_id_up is None
    assert mstate.order_id_down is None
    assert mstate.entry_cancelled_timeout is False
    assert "delayed" in mstate.last_action


def test_issue137_delay_allows_quoting_after_expiry():
    """Same window quotes normally once elapsed passes the delay."""
    engine = _live_engine(entry_delay_sec=60.0)
    slug = "btc-up-or-down-5m"
    engine._update_market_strategy(slug, _poll_data(1000.0), now=1005.0)
    assert engine.place_live_quote.call_count == 0
    engine._update_market_strategy(slug, _poll_data(1000.0), now=1061.0)
    mstate = engine.markets[slug]
    assert mstate.order_id_up is not None
    assert mstate.order_id_down is not None
    assert mstate.order_status_up == "RESTING"
    assert mstate.order_status_down == "RESTING"


def test_issue137_no_delay_quotes_immediately_by_default():
    """Defaults (delay 0) quote on the first tick — behavior unchanged."""
    engine = _live_engine()
    slug = "btc-up-or-down-5m"
    engine._update_market_strategy(slug, _poll_data(1000.0), now=1005.0)
    mstate = engine.markets[slug]
    assert mstate.order_id_up is not None
    assert mstate.order_id_down is not None


# ============================================================================
# TASK 3: post-delay entry-band gate + telemetry
# ============================================================================

def test_issue137_band_failure_skips_window_without_orders():
    """Drifted mid after the delay skips the window; adverse gate stays out."""
    engine = _live_engine(entry_delay_sec=60.0, entry_band=0.04, exit_thresh=0.20)
    slug = "btc-up-or-down-5m"
    engine._update_market_strategy(slug, _poll_data(1000.0, mid=0.50), now=1005.0)
    assert engine.place_live_quote.call_count == 0
    # Delay expired (61s), mid 0.60 -> drift 0.10 > band 0.04, < exit 0.20.
    engine._update_market_strategy(slug, _poll_data(1000.0, mid=0.60), now=1061.0)
    mstate = engine.markets[slug]
    assert engine.place_live_quote.call_count == 0
    assert mstate.entry_cancelled_timeout is True
    assert mstate.band_skip is True
    assert mstate.adverse_open is False
    assert mstate.status == "BAND_SKIPPED"
    assert "band" in mstate.last_action.lower()


def test_issue137_band_pass_quotes_after_delay():
    """Mid still near 0.50 after the delay quotes normally."""
    engine = _live_engine(entry_delay_sec=60.0, entry_band=0.04)
    slug = "btc-up-or-down-5m"
    engine._update_market_strategy(slug, _poll_data(1000.0, mid=0.50), now=1005.0)
    engine._update_market_strategy(slug, _poll_data(1000.0, mid=0.52), now=1061.0)
    mstate = engine.markets[slug]
    assert mstate.band_gate_evaluated is True
    assert mstate.band_skip is False
    assert mstate.order_status_up == "RESTING"
    assert mstate.order_status_down == "RESTING"


def test_issue137_band_waits_for_two_sided_book():
    """A one-sided book at delay expiry waits; the next two-sided tick decides."""
    engine = _live_engine(entry_delay_sec=60.0, entry_band=0.04, exit_thresh=0.20)
    slug = "btc-up-or-down-5m"
    one_sided = _poll_data(1000.0, mid=0.60,
                           down_book={"best_bid": None, "best_ask": None})
    # Early one-sided tick latches a healthy first-seen time; delay holds.
    engine._update_market_strategy(slug, _poll_data(1000.0, mid=0.50), now=1005.0)
    # Delay expired but the book is one-sided: hold quotes, latch nothing.
    engine._update_market_strategy(slug, one_sided, now=1061.0)
    mstate = engine.markets[slug]
    assert mstate.band_gate_evaluated is False
    assert mstate.entry_cancelled_timeout is False
    assert engine.place_live_quote.call_count == 0
    assert "waiting for two-sided book" in mstate.last_action
    engine._update_market_strategy(slug, _poll_data(1000.0, mid=0.60), now=1062.0)
    assert mstate.band_gate_evaluated is True
    assert mstate.band_skip is True
    assert mstate.status == "BAND_SKIPPED"


def test_issue137_band_does_not_gate_reentry():
    """An adverse-open skip still re-enters when the mid reverts — band exempt."""
    engine = _live_engine(entry_band=0.04)
    slug = "btc-up-or-down-5m"
    engine._update_market_strategy(slug, _poll_data(1000.0, mid=0.60), now=1000.0)
    mstate = engine.markets[slug]
    assert mstate.adverse_open is True
    assert mstate.status == "DRIFT_SKIPPED"
    assert mstate.band_skip is False
    # Mid reverts to 0.50 with 240s left: re-entry must quote despite the band.
    engine._update_market_strategy(slug, _poll_data(1000.0, mid=0.50), now=1060.0)
    assert mstate.reentry_count == 1
    assert mstate.status == "QUOTING"
    assert mstate.order_status_up == "RESTING"
    assert mstate.order_status_down == "RESTING"


def test_issue137_band_skip_telemetry_echoed_in_state():
    """Band skips increment a session counter visible in get_state()."""
    engine = _live_engine(entry_delay_sec=60.0, entry_band=0.04, exit_thresh=0.20)
    slug = "btc-up-or-down-5m"
    engine._update_market_strategy(slug, _poll_data(1000.0, mid=0.50), now=1005.0)
    engine._update_market_strategy(slug, _poll_data(1000.0, mid=0.60), now=1061.0)
    state = engine.get_state()
    assert state["band_skip_stats"]["band_skips"] == 1
    assert state["markets"][slug]["band_skip"] is True


# ============================================================================
# TASK 4: stop_loss_enabled gate
# ============================================================================

def test_issue137_place_stop_order_noop_when_disabled():
    """place_stop_order stages nothing when stop_loss_enabled is False."""
    engine = LiveTraderEngine()
    engine.update_config(stop_loss_enabled=False)
    slug = "btc-up-or-down-5m"
    mstate = engine.markets[slug]
    mstate.fill_price_up = 0.48
    mstate.up_token = "tok_up_137"
    engine.place_stop_order(mstate, "UP")
    assert mstate.stop_order_id is None
    assert mstate.stop_order_status == "NONE"

    control = LiveTraderEngine()
    cstate = control.markets[slug]
    cstate.fill_price_up = 0.48
    cstate.up_token = "tok_up_137"
    control.place_stop_order(cstate, "UP")
    assert cstate.stop_order_id is not None


def _fill_up_naked(engine: LiveTraderEngine, slug: str):
    """Fill UP naked across two paper ticks.

    Tick 1 quotes into a healthy book (resting UP latches at 0.48); tick 2
    shifts the UP book down so its ask (0.47) lifts the resting bid.
    """
    engine._update_market_strategy(
        slug, _side_books(1000.0, 0.49, 0.51, 0.49, 0.51), now=1000.0)
    engine._update_market_strategy(
        slug, _side_books(1000.0, 0.45, 0.47, 0.49, 0.51), now=1001.0)
    mstate = engine.markets[slug]
    assert mstate.filled_up is True
    assert mstate.filled_down is False
    return mstate


def test_issue137_naked_leg_held_when_stop_disabled():
    """Disabled stop: fill stages no stop and adverse drift never exits."""
    engine = _paper_engine(stop_loss_enabled=False)
    slug = "btc-up-or-down-5m"
    mstate = _fill_up_naked(engine, slug)
    assert mstate.stop_order_id is None
    # Mid collapses to 0.38: drift 0.12, far past the 0.05 naked stop.
    engine._update_market_strategy(
        slug, _side_books(1000.0, 0.36, 0.38, 0.60, 0.62), now=1002.0)
    assert mstate.exit_taken is False
    assert mstate.filled_up is True
    assert mstate.status != "STOP_EXIT_PENDING"


def test_issue137_drift_stop_still_fires_when_enabled():
    """Control: default engine exits the same adverse drift via stop."""
    engine = _paper_engine()
    slug = "btc-up-or-down-5m"
    mstate = _fill_up_naked(engine, slug)
    assert mstate.stop_order_id is not None
    engine._update_market_strategy(
        slug, _side_books(1000.0, 0.36, 0.38, 0.60, 0.62), now=1002.0)
    assert mstate.exit_taken is True


# ============================================================================
# TASK 5: preset application (engine side)
# ============================================================================

PATIENT_UNIVERSE = {"xrp-up-or-down-15m", "bnb-up-or-down-15m", "eth-up-or-down-5m"}


def test_issue137_preset_applies_all_fields_atomically():
    """preset= selects every research-winning value plus the pilot universe."""
    engine = LiveTraderEngine()
    engine.update_config(preset="patient_band_maker")
    assert engine.offset == 0.03
    assert engine.entry_band == 0.04
    assert engine.entry_delay_sec == 60.0
    assert engine.stop_loss_enabled is False
    assert engine.max_pair_cost == 0.98
    assert {s[0] for s in engine.selected_series} == PATIENT_UNIVERSE
    assert set(engine.markets.keys()) == PATIENT_UNIVERSE
    assert engine.active_preset == "patient_band_maker"
    state = engine.get_state()
    assert state["active_preset"] == "patient_band_maker"
    assert state["params"]["entry_delay_sec"] == 60.0


def test_issue137_manual_divergence_clears_active_preset():
    """A manual knob change away from the table drops the preset latch."""
    engine = LiveTraderEngine()
    engine.update_config(preset="patient_band_maker")
    assert engine.active_preset == "patient_band_maker"
    engine.update_config(offset=0.02)
    assert engine.active_preset is None
    assert engine.offset == 0.02


def test_issue137_matching_manual_set_keeps_active_preset():
    """Re-asserting a preset value keeps the latch (no false invalidation)."""
    engine = LiveTraderEngine()
    engine.update_config(preset="patient_band_maker")
    engine.update_config(offset=0.03)
    assert engine.active_preset == "patient_band_maker"
