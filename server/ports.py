"""Canonical dashboard network location (single source of truth).

Issue #313: exactly one tracked place defines the dashboard port.
Python consumers import from here; the PowerShell launcher probes it via
``python -c "from server.ports import DASHBOARD_PORT; print(DASHBOARD_PORT)"``.

Deliberately dependency-free (stdlib only) so ``scripts/observe_paper.py``
can import it without pulling ``server.osc_dash`` (which imports the live
trader + backtest stack).
"""

DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 5515
DASHBOARD_URL = f"http://{DASHBOARD_HOST}:{DASHBOARD_PORT}"
