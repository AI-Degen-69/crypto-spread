"""Backtest template store and serialization helpers.

Manages saving, loading, listing, and validating named parameter+result
templates in a local directory (typically run/backtest_templates/).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from strategy.windows import write_json_atomic

# Allowed template name: alphanumeric, dash, underscore, dot, space (max 64 chars)
_VALID_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-\. ]{1,64}$")

REQUIRED_RECORD_KEYS = {
    "name",
    "saved_at",
    "run_id",
    "query",
    "request_args",
    "echo",
    "params_dict",
    "params_hash",
    "dataset",
    "scope",
    "summary",
}

REQUIRED_SUMMARY_KEYS = {
    "params_hash",
    "n_windows",
    "total_pnl_cents",
    "pairs",
    "win_rate",
    "selection",
}


def normalize_name(name: str) -> str:
    """Validate and normalize a template name.

    Rejects path traversal, special characters, and empty names.
    """
    if not isinstance(name, str):
        raise ValueError("template name must be a string")
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("template name cannot be empty")
    if ".." in cleaned or "/" in cleaned or "\\" in cleaned:
        raise ValueError("invalid template name: path traversal characters forbidden")
    if not _VALID_NAME_RE.match(cleaned):
        raise ValueError(
            f"invalid template name '{cleaned}': allowed characters are a-z, A-Z, 0-9, _, -, ., space (max 64 chars)"
        )
    return cleaned


def template_path(directory: Path | str, name: str) -> Path:
    """Return the filesystem path for a template name in directory."""
    norm = normalize_name(name)
    return Path(directory) / f"{norm}.json"


def validate_record(record: dict[str, Any]) -> dict[str, Any]:
    """Validate that record contains all required top-level and summary keys.

    Values may be null / None. Raises ValueError if structure is invalid.
    """
    if not isinstance(record, dict):
        raise ValueError("template record must be a dict")
    missing_top = REQUIRED_RECORD_KEYS - set(record.keys())
    if missing_top:
        raise ValueError(f"missing required record keys: {sorted(missing_top)}")
    summary = record.get("summary")
    if summary is None or not isinstance(summary, dict):
        raise ValueError("record['summary'] must be a dict")
    missing_sum = REQUIRED_SUMMARY_KEYS - set(summary.keys())
    if missing_sum:
        raise ValueError(f"missing required summary keys: {sorted(missing_sum)}")
    return record


def save_template(directory: Path | str, name: str, record: dict[str, Any]) -> bool:
    """Validate and atomically save record to directory/<name>.json.

    Returns True if an existing template file was overwritten, False if newly created.
    """
    validate_record(record)
    target = template_path(directory, name)
    existed = target.exists()
    write_json_atomic(target, record)
    return existed


def read_template(directory: Path | str, name: str) -> dict[str, Any]:
    """Read and validate template record from disk.

    Raises FileNotFoundError if missing, ValueError if invalid/corrupt.
    """
    target = template_path(directory, name)
    if not target.exists():
        raise FileNotFoundError(f"template not found: {name}")
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"malformed template JSON for '{name}': {exc}") from exc
    return validate_record(data)


def list_templates(directory: Path | str) -> list[dict[str, Any]]:
    """List all templates in directory sorted newest saved_at first.

    Returns [] if directory does not exist. Corrupted/unreadable files
    are reported with {'name': stem, 'invalid': True}.
    """
    dir_path = Path(directory)
    if not dir_path.exists() or not dir_path.is_dir():
        return []

    results: list[dict[str, Any]] = []
    for p in dir_path.glob("*.json"):
        stem_name = p.stem
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            validate_record(raw)
            summary = raw.get("summary") or {}
            results.append({
                "name": raw.get("name") or stem_name,
                "saved_at": raw.get("saved_at"),
                "run_id": raw.get("run_id"),
                "params_hash": raw.get("params_hash"),
                "n_windows": summary.get("n_windows"),
                "total_pnl_cents": summary.get("total_pnl_cents"),
                "pairs": summary.get("pairs"),
                "win_rate": summary.get("win_rate"),
                "selection": summary.get("selection"),
                "invalid": False,
            })
        except Exception:
            results.append({
                "name": stem_name,
                "invalid": True,
            })

    # Sort valid records newest saved_at first, invalid records last
    def _sort_key(item: dict[str, Any]) -> tuple[int, float]:
        """Order list rows: valid templates newest-first, unreadable files last."""
        if item.get("invalid"):
            return (0, 0.0)
        saved = item.get("saved_at")
        try:
            return (1, float(saved) if saved is not None else 0.0)
        except (ValueError, TypeError):
            return (1, 0.0)

    results.sort(key=_sort_key, reverse=True)
    return results


def delete_template(directory: Path | str, name: str) -> None:
    """Delete a template file from disk.

    Raises FileNotFoundError if missing.
    """
    target = template_path(directory, name)
    if not target.exists():
        raise FileNotFoundError(f"template not found: {name}")
    target.unlink()
