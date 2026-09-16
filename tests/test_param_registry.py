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
    # Issue #228: entry_band and reentry_* are inert on BacktestParams pending
    # T5 removal of their last senders (scripts, sims); they are deliberately
    # absent from the UI registry.
    _INERT_PENDING_T5 = {
        "entry_band", "reentry_drift_band", "min_requote_remaining_sec",
        "reentry_min_remaining_pct", "max_reentries_per_window"
    }
    declared = {f.name for f in fields(BacktestParams)} - _INERT_PENDING_T5
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


def test_every_raw_entry_has_the_expected_tuple_shape():
    """A short tuple would unpack-error only when param_spec() is first called.

    Six elements, plus an optional seventh holding per-surface bound overrides
    for knobs whose research and live ranges legitimately differ.
    """
    for group, entries in BacktestParams._PARAM_GROUPS.items():
        for entry in entries:
            assert len(entry) in (6, 7), (
                f"{group} entry {entry[0]!r} has {len(entry)} elements, need 6 "
                "(field, label, why, unit, bounds, surfaces) or 7 with "
                "per-surface overrides")
            if len(entry) == 7:
                assert isinstance(entry[6], dict), (
                    f"{group} entry {entry[0]!r}: the 7th element must map "
                    "surface -> (low, high)")
                assert set(entry[6]) <= set(entry[5]), (
                    f"{group} entry {entry[0]!r} overrides a surface it does "
                    "not declare")


def test_every_default_lies_inside_its_own_bounds():
    """A default outside its advertised range is a form that rejects its own value.

    Caught exactly this: `pair_cost_gate` defaulted to 1.05, and copying live's
    `max_pair_cost` range of (0.50, 1.00) onto it made the registry advertise a
    range excluding the engine's own default. Issue #227 resolved it the other
    way — one field, one default of 0.99, inside one range.
    """
    live = BacktestParams()
    bad = []
    for group in SPEC.values():
        for name, spec in group.items():
            bounds = spec["bounds"]
            if bounds is None:
                continue
            value = getattr(live, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            low, high = bounds
            if not (low <= value <= high):
                bad.append((name, value, bounds))
    assert bad == [], f"defaults outside their own bounds: {bad}"


def test_per_surface_bounds_never_widen_the_shared_range():
    """An override exists to tighten a surface, never to loosen it."""
    for group in SPEC.values():
        for name, spec in group.items():
            base = spec["bounds"]
            for surface, b in spec.get("surface_bounds", {}).items():
                assert base is not None, f"{name} overrides a surface but has no base bounds"
                assert b[0] >= base[0] and b[1] <= base[1], (
                    f"{name}[{surface}] = {b} is wider than the shared {base}")
                assert BacktestParams.bounds_for(name, surface) == b


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


def test_the_registry_does_not_claim_post_init_enforces_every_bound():
    """`bounds` describes request validation, not dataclass construction.

    Review flagged the docstring as false: twelve registry-bounded fields have
    no `__post_init__` check, and every driver in `research/sweeps/` builds
    `BacktestParams` directly, bypassing the API clamp. Rather than add
    validation that would reject configurations those sweeps legitimately use,
    the claim was corrected. This pins that it stays corrected.

    The worked example used to be `pair_cost_gate`. Issue #227 made that one a
    validated structural limit, so the example moved to `queue_gate`, which is
    still registry-bounded and still unchecked at construction.
    """
    doc = BacktestParams.param_spec.__doc__ or ""
    assert "NOT a claim about" in doc, (
        "param_spec's docstring no longer states that bounds are request "
        "validation rather than dataclass validation")
    # And the gap it describes is real: a direct construction outside bounds
    # still succeeds, which is exactly why the wording matters.
    low, high = BacktestParams.spec_for("queue_gate")["bounds"]
    assert BacktestParams(queue_gate=high + 1.0).queue_gate == high + 1.0


@pytest.mark.parametrize("name,low,high", [
    ("entry_timeout_pct", 0.0, 1.0),
    ("max_start_elapsed_pct", 0.0, 1.0),
    ("entry_delay_sec", 0.0, 3600.0),
    ("max_pair_cost", 0.50, 1.00),
])
def test_registered_bounds_match_post_init_validation(name, low, high):
    """The UI must refuse exactly what the engine refuses, not a wider range."""
    spec = next(g[name] for g in SPEC.values() if name in g)
    assert spec["bounds"] == (low, high), (
        f"{name} registry bounds {spec['bounds']} disagree with __post_init__")
    # And the engine really does reject just outside them.
    with pytest.raises(ValueError):
        BacktestParams(**{name: high + 0.01})


def test_quote_range_registered_bounds_match_post_init_validation():
    """quote_range bounds [0.0, 1.0] match __post_init__ validation."""
    spec = next(g["quote_range"] for g in SPEC.values() if "quote_range" in g)
    assert spec["bounds"] == (0.0, 1.0)
    with pytest.raises(ValueError):
        BacktestParams(quote_range=(-0.01, 0.90))
    with pytest.raises(ValueError):
        BacktestParams(quote_range=(0.10, 1.01))
    with pytest.raises(ValueError):
        BacktestParams(quote_range=(0.90, 0.10))


def test_knobs_the_live_engine_exposes_are_marked_for_the_cockpit():
    """The Cockpit exposes every trading knob settable on the live engine."""
    for name in ("entry_delay_sec", "max_pair_cost", "offset",
                 "quote_shares", "entry_timeout_pct", "exit_reversal",
                 "quote_range"):
        spec = next(g[name] for g in SPEC.values() if name in g)
        assert "cockpit" in spec["surfaces"], (
            f"{name} is settable on the live engine but not marked for the Cockpit")


def test_model_side_assumptions_are_not_offered_as_live_knobs():
    """Venue fees and price granularity are not set by an operator on the book.

    `fill_model` used to head this list. Issue #226 removed it outright: how a
    venue fills you is not an assumption to tune, it is one rule (ADR-0002).
    """
    for name in ("taker_fee_rate", "tick_size", "merge_gas_usd"):
        spec = next(g[name] for g in SPEC.values() if name in g)
        assert "cockpit" not in spec["surfaces"], (
            f"{name} is an execution assumption and must not appear in the Cockpit")
        assert "backtest" in spec["surfaces"]


def test_grouped_params_still_works_unchanged():
    """Existing consumers of the older view must not break."""
    g = BacktestParams().grouped_params()
    assert g["trading_knobs"]["offset"] == pytest.approx(0.020)
    assert g["trading_knobs"]["exit_default_5m"] == pytest.approx(0.05)
    assert "taker_fee_rate" in g["execution_assumptions"]


def test_zero_is_no_longer_a_way_to_switch_the_pair_cost_cap_off():
    """0.0 used to disable the gate. It is now simply out of range (#227).

    The lower bound matters more than the upper one for muscle memory: every
    driver written against `pair_cost_gate` could pass 0.0 to mean "no cap",
    and under the new field that reads as a cap of zero, which would stop the
    chase dead on every window instead of freeing it. It raises.
    """
    for off in (0.0, 0.49):
        with pytest.raises(ValueError, match="max_pair_cost"):
            BacktestParams(max_pair_cost=off)
    # And the bound itself is inclusive on both ends.
    assert BacktestParams(max_pair_cost=0.50).max_pair_cost == 0.50
    assert BacktestParams(max_pair_cost=1.00).max_pair_cost == 1.00
