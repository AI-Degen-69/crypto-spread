"""Venue-host constants and import guard for the book layer (Issue #373).

`strategy.markets` is the single source of truth for the book/tape fetchers,
so it also has to own the venue hosts those fetchers are told to talk to.
Until this issue, `scripts/record_raw_socket_session.py` imported `CLOB_HOST`
from it and had raised `ImportError` since the day it was written -- nothing in
tests or CI imported that module, so nothing caught it.

These tests are offline: they only import modules and compare strings.
"""
from __future__ import annotations

import contextlib
import importlib
import io
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Mirrors the canonical pair in `scripts/collect_ticks.py`. Retyped on purpose:
# if they ever drift apart this test fails and says which side moved.
CANONICAL_GAMMA_HOST = "https://gamma-api.polymarket.com"
CANONICAL_CLOB_HOST = "https://clob.polymarket.com"


def test_venue_host_constants_exist_and_match_canonical():
    """`strategy.markets` exposes both hosts with the canonical values."""
    from strategy import markets

    assert markets.GAMMA_HOST == CANONICAL_GAMMA_HOST
    assert markets.CLOB_HOST == CANONICAL_CLOB_HOST


def test_collector_and_book_layer_agree_on_hosts():
    """The canonical collector and the book layer cannot drift apart.

    #374 rewires `collect_ticks` onto these constants; this asserts the
    agreement that rewire depends on, rather than trusting two copies of the
    same literal.
    """
    from scripts import collect_ticks
    from strategy import markets

    assert markets.CLOB_HOST == collect_ticks.CLOB_HOST
    assert markets.GAMMA_HOST == collect_ticks.GAMMA_HOST


def test_socket_session_recorder_imports():
    """The raw socket-session recorder starts (the regression of Issue #373)."""
    module = importlib.import_module("scripts.record_raw_socket_session")
    assert module.CLOB_HOST == CANONICAL_CLOB_HOST


def _repo_script_modules() -> list[str]:
    """Every module directly under `scripts/`, repo-local only.

    `scripts` is a namespace package that also swallows `site-packages`
    (pywin32 ships a `scripts` package too), so the directory is globbed
    rather than iterated via `pkgutil` -- otherwise CI would import whatever
    happens to be installed on the runner.
    """
    return sorted(p.stem for p in (ROOT / "scripts").glob("*.py")
                  if p.stem != "__init__")


@pytest.mark.parametrize("module_name", _repo_script_modules())
def test_every_repo_script_imports(module_name: str):
    """A missing name in a shared module fails the suite, not a human.

    This is the class-wide guard the Issue #373 defect belongs to: the
    recorder's `ImportError` survived for as long as it did because no test
    imported it. Import only -- no network, no `main()`, stdout suppressed so
    module-level chatter does not pollute the test report.
    """
    with contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        importlib.import_module(f"scripts.{module_name}")
