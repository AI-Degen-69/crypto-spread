"""Run-folder layout helpers (issue #147, Stage 1).

Single source of truth for the /runs convention:
    runs/{paper|live}/YYYY-MM-DD_HH-MM_TZ/{data/,research-papers/,summary.html,manifest.json}

- paper/ = SIMULATION/PAPER (no real money). live/ = real orders, real money.
  The split is enforced in code (ValueError), not just docs.
- 24h local operator time, no colons (illegal on Windows), year mandatory.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Literal
import re

ROOT = Path(__file__).resolve().parent.parent
RUNS_ROOT = ROOT / "runs"

RunKind = Literal["paper", "live"]

_MANIFEST_KEYS = (
    "kind", "run_id", "started_local", "started_utc", "stopped_utc", "tz",
    "preset", "config_hypothesis", "planned_hours", "final", "data",
    "papers", "summary",
)
_FINAL_KEYS = ("total_pnl", "realized_pnl", "total_trades", "win_rate",
               "pairs_merged", "stops_triggered")
_PAPER_KEYS = ("abstract_and_methodology", "results_and_findings",
               "conclusions_and_projections")

_SAFE_TZ_RE = re.compile(r"^[A-Za-z]{2,5}$")
_SAFE_SLUG_RE = re.compile(r"^[a-z0-9-]+$")


def local_tz_abbr() -> str:
    """Local zone abbreviation from the machine (e.g. IDT/IST)."""
    return datetime.now().astimezone().tzname() or "UTC"


def new_run_dir(kind: RunKind | str, start: datetime, tz_abbr: str,
                root: Path | None = None) -> Path:
    """Create runs/{kind}/YYYY-MM-DD_HH-MM_TZ/ with data/ + research-papers/."""
    if kind not in ("paper", "live"):
        raise ValueError(f"kind must be 'paper' or 'live', got {kind!r}")
    if not _SAFE_TZ_RE.match(tz_abbr or ""):
        raise ValueError(f"bad TZ abbreviation: {tz_abbr!r}")
    base = root if root is not None else RUNS_ROOT
    run_id = f"{start:%Y-%m-%d_%H-%M}_{tz_abbr}"
    d = base / kind / run_id
    (d / "data").mkdir(parents=True, exist_ok=True)
    (d / "research-papers").mkdir(parents=True, exist_ok=True)
    return d


def write_manifest(run_dir: Path, payload: dict) -> Path:
    """Validate the §6 schema and write manifest.json (machine-readable)."""
    missing = [k for k in _MANIFEST_KEYS if k not in payload]
    if missing:
        raise ValueError(f"manifest missing keys: {missing}")
    missing = [k for k in _FINAL_KEYS if k not in payload["final"]]
    if missing:
        raise ValueError(f"manifest.final missing keys: {missing}")
    missing = [k for k in _PAPER_KEYS if k not in payload["papers"]]
    if missing:
        raise ValueError(f"manifest.papers missing keys: {missing}")
    p = run_dir / "manifest.json"
    p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return p


def write_paper_stub(run_dir: Path, name: str, title: str,
                     body_html: str) -> Path:
    """Write a minimal themed lifecycle paper shell into research-papers/."""
    if not _SAFE_SLUG_RE.match(name or ""):
        raise ValueError(f"bad paper name: {name!r}")
    p = run_dir / "research-papers" / f"{name}.html"
    p.write_text(
        "<!doctype html><html lang=\"he\" dir=\"rtl\"><head><meta charset=\"utf-8\">"
        f"<title>{title}</title></head><body><h1>{title}</h1>\n"
        f"{body_html}\n</body></html>",
        encoding="utf-8",
    )
    return p


def write_summary_html(run_dir: Path, ctx: dict) -> Path:
    """Write the run-level summary.html (config + pointers to data/papers)."""
    for k in ("title", "run_id", "kind", "started_local",
              "config_hypothesis", "papers", "data"):
        if k not in ctx:
            raise ValueError(f"summary ctx missing key: {k}")
    papers = "\n".join(f"<li><a href=\"{v}\">{k}</a></li>"
                       for k, v in ctx["papers"].items())
    data = "\n".join(f"<li><a href=\"{v}\">{v}</a></li>" for v in ctx["data"])
    p = run_dir / "summary.html"
    p.write_text(
        "<!doctype html><html lang=\"he\" dir=\"rtl\"><head><meta charset=\"utf-8\">"
        f"<title>{ctx['title']}</title></head><body>"
        f"<h1>{ctx['title']}</h1>"
        f"<p>run_id={ctx['run_id']} kind={ctx['kind']} started={ctx['started_local']}</p>"
        f"<h2>config</h2><pre>{json.dumps(ctx['config_hypothesis'], indent=2)}</pre>"
        f"<h2>papers</h2><ul>{papers}</ul>"
        f"<h2>data</h2><ul>{data}</ul></body></html>",
        encoding="utf-8",
    )
    return p
