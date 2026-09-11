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
