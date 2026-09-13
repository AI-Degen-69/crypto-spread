"""Issue #164: the parameter registry is the single source of truth.

Backtest and Cockpit drifted because each hand-rolled its own labels, defaults
and bounds. These tests pin the registry as the one definition, so a knob added
to the engine cannot silently miss a surface and a label cannot be copied into
a second file.
"""
from dataclasses import fields

import pytest

from backtest.engine import BacktestParams


SPEC = BacktestParams.param_spec()


def test_every_engine_field_is_registered_exactly_once():
    """A field absent from the registry is invisible to both UIs."""
    declared = {f.name for f in fields(BacktestParams)}
    registered = [name for group in SPEC.values() for name in group]
    assert len(registered) == len(set(registered)), (
        "a field is registered in more than one group: "
        f"{sorted({n for n in registered if registered.count(n) > 1})}")
    missing = sorted(declared - set(registered))
    assert missing == [], f"engine fields missing from the registry: {missing}"
    extra = sorted(set(registered) - declared)
    assert extra == [], f"registry names no such engine field: {extra}"


def test_registry_cannot_name_a_field_that_does_not_exist():
    """`param_spec` drops unknown names silently — so nothing else may.

    The drop is deliberate (a new field must not break grouping on a missing
    entry), but it also means a registry entry for a knob that was never
    implemented disappears with no error, and every other test here passes
    vacuously for it. Read the raw groups, not the filtered spec.
    """
    declared = {f.name for f in fields(BacktestParams)}
    phantom = sorted(
        entry[0]
        for entries in BacktestParams._PARAM_GROUPS.values()
        for entry in entries
        if entry[0] not in declared
    )
    assert phantom == [], (
        f"registry names fields the dataclass does not have: {phantom} — "
        "they are silently dropped from param_spec(), so no UI renders them "
        "and no test covers them")


def test_every_raw_entry_has_the_full_six_tuple_shape():
    """A short tuple would unpack-error only when param_spec() is first called."""
    for group, entries in BacktestParams._PARAM_GROUPS.items():
        for entry in entries:
            assert len(entry) == 6, (
                f"{group} entry {entry[0]!r} has {len(entry)} elements, need 6 "
                "(field, label, why, unit, bounds, surfaces)")


def test_every_entry_carries_what_a_surface_needs_to_render_it():
    """Label, unit, default and bounds all live here, or a UI re-invents them."""
    for group, entries in SPEC.items():
        for name, spec in entries.items():
            for key in ("label", "why", "unit", "default", "bounds", "surfaces"):
                assert key in spec, f"{group}.{name} is missing {key!r}"
            assert spec["label"].strip(), f"{group}.{name} has an empty label"
            assert isinstance(spec["surfaces"], tuple), f"{group}.{name} surfaces must be a tuple"
            assert set(spec["surfaces"]) <= {"backtest", "cockpit"}, (
                f"{group}.{name} names an unknown surface: {spec['surfaces']}")


def test_registered_defaults_match_the_dataclass_defaults():
    """A registry default that drifts from the dataclass is a lie in the UI."""
    live = BacktestParams()
    drift = []
    for entries in SPEC.values():
        for name, spec in entries.items():
            actual = getattr(live, name)
            if spec["default"] != actual:
                drift.append((name, spec["default"], actual))
    assert drift == [], f"registry default != dataclass default: {drift}"


@pytest.mark.parametrize("name,low,high", [
    ("entry_timeout_pct", 0.0, 1.0),
    ("max_start_elapsed_pct", 0.0, 1.0),
    ("reentry_drift_band", 0.0, 0.5),
    ("reentry_min_remaining_pct", 0.0, 1.0),
    ("entry_delay_sec", 0.0, 3600.0),
    ("entry_band", 0.0, 0.50),
])
def test_registered_bounds_match_post_init_validation(name, low, high):
    """The UI must refuse exactly what the engine refuses, not a wider range."""
    spec = next(g[name] for g in SPEC.values() if name in g)
    assert spec["bounds"] == (low, high), (
        f"{name} registry bounds {spec['bounds']} disagree with __post_init__")
    # And the engine really does reject just outside them.
    with pytest.raises(ValueError):
        BacktestParams(**{name: high + 0.01})


def test_knobs_the_live_engine_exposes_are_marked_for_the_cockpit():
    """The Cockpit had no input for the two knobs that define the preset."""
    for name in ("entry_delay_sec", "entry_band", "reentry_drift_band",
                 "min_requote_remaining_sec", "pair_cost_gate", "offset",
                 "quote_shares", "entry_timeout_pct", "exit_reversal"):
        spec = next(g[name] for g in SPEC.values() if name in g)
        assert "cockpit" in spec["surfaces"], (
            f"{name} is settable on the live engine but not marked for the Cockpit")


def test_model_side_assumptions_are_not_offered_as_live_knobs():
    """Fill model and venue fees are not things an operator sets on the book."""
    for name in ("fill_model", "taker_fee_rate", "tick_size", "merge_gas_usd"):
        spec = next(g[name] for g in SPEC.values() if name in g)
        assert "cockpit" not in spec["surfaces"], (
            f"{name} is an execution assumption and must not appear in the Cockpit")
        assert "backtest" in spec["surfaces"]


def test_grouped_params_still_works_unchanged():
    """Existing consumers of the older view must not break."""
    g = BacktestParams().grouped_params()
    assert g["trading_knobs"]["offset"] == pytest.approx(0.020)
    assert g["trading_knobs"]["exit_default_5m"] == pytest.approx(0.05)
    assert "fill_model" in g["execution_assumptions"]
