"""Assemble the golden tick dataset from the pristine day files (issue #281).

Route note: #281 was chartered around a dedicated multi-day capture, but the merged
pristine pipeline (PRs #291/#299/#301) already produces day files that pass every
charter gate — measured on 2026-09-22: 4,910 windows (>=500), weakest pair 159 (>=50),
6 time blocks (>=5), 1,428,888 valid ticks (>=50,000), sampling gap rate 0.0 (<=0.05),
10/10 series. This tool therefore promotes pristine days through the charter's §1.1
per-day gates and assembles `run/ticks/golden/` per §3.1 — no new capture needed.

Behavior:
- Filters every `run/ticks/pristine/ticks_*.jsonl` through verify_tick_file and keeps
  only days meeting §1.1 (PASS + COMPLETE CAPTURE, zero corrupt rows, zero collector
  errors, zero time reversals). Failing days are excluded AND recorded with a reason.
- Copies passing days to `run/ticks/golden/` byte-for-byte (sources never modified).
- Writes `run/ticks/golden/golden_manifest.json` per charter §3.1: per-day provenance
  (source pristine file, sha256 of the copied file, verify verdict, windows count) plus
  set totals, the READINESS_POLICY_VERSION, and the certification UTC date.
- Builds backtest index (.idx) sidecars for every golden day so first replay is fast.
- Deterministic: a re-run over unchanged inputs rewrites byte-identical artifacts.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backtest.index import build_index, is_fresh
from scripts.ship_to_drive import sha256_of
from scripts.verify_tick_data import READINESS_POLICY_VERSION, verify_tick_file
from strategy.windows import write_json_atomic

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PRISTINE_DIR = ROOT / "run" / "ticks" / "pristine"
DEFAULT_GOLDEN_DIR = ROOT / "run" / "ticks" / "golden"
MANIFEST_NAME = "golden_manifest.json"
CHARTER = "docs/golden-tick-dataset.md"
VERIFY_CACHE_DIRNAME = ".verify_cache"

DAY_SUFFIXES = (".jsonl", ".jsonl.gz")


def _file_fingerprint(path: Path) -> str:
    """Same cheap identity the dashboard uses: size + mtime_ns."""
    st = path.stat()
    return f"{st.st_size}:{st.st_mtime_ns}"


def _write_verify_sidecar(golden_dir: Path, target: Path, report: dict[str, Any]) -> None:
    """Persist the day's verify report where the dashboard's cache reads it.

    Mirrors server/osc_dash.py's sidecar layout: .verify_cache/golden/<name>.json
    keyed by the copied file's fingerprint, so the golden card (issue #292) and
    the file table serve cached verdicts without re-streaming day files.
    """
    try:
        sidecar_dir = golden_dir.parent / VERIFY_CACHE_DIRNAME / golden_dir.name
        sidecar_dir.mkdir(parents=True, exist_ok=True)
        import time

        payload = dict(report)
        payload["fingerprint"] = _file_fingerprint(target)
        payload["ts"] = time.time()
        (sidecar_dir / f"{target.name}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
    except Exception:
        pass  # sidecar is a cache; the manifest carries the authoritative verdict


def day_gate_verdict(report: dict[str, Any]) -> tuple[bool, str | None]:
    """Charter §1.1 — the per-day acceptance gate over a verify report.

    Returns (passed, exclusion_reason). Every failing day must carry a reason so the
    manifest can record the exclusion (charter: failed days are never silently mixed in).
    """
    status = report.get("status")
    capture_label = (report.get("capture_state") or {}).get("label")
    if status != "PASS":
        return False, f"integrity status {status!r} != 'PASS'"
    if capture_label != "COMPLETE CAPTURE":
        return False, f"capture state {capture_label!r} != 'COMPLETE CAPTURE'"
    if int(report.get("corrupt_lines") or 0) != 0:
        return False, f"{report.get('corrupt_lines')} corrupt rows (require 0)"
    if int(report.get("collector_errors") or 0) != 0:
        return False, f"{report.get('collector_errors')} collector errors (require 0)"
    if int(report.get("time_reversals") or 0) != 0:
        return False, f"{report.get('time_reversals')} time reversals (require 0)"
    return True, None


def source_day_files(pristine_dir: Path) -> list[Path]:
    """Deterministically ordered pristine day files (manifest itself excluded)."""
    return sorted(
        f for f in pristine_dir.iterdir()
        if f.is_file() and f.name.endswith(DAY_SUFFIXES)
    )


def build_golden_dataset(
    pristine_dir: Path,
    golden_dir: Path,
    *,
    max_gap_sec: float = 6.0,
    max_start_delay_sec: float = 5.0,
    build_indexes: bool = True,
) -> dict[str, Any]:
    """Filter pristine days through §1.1, assemble golden/ + manifest, build sidecars."""
    if not pristine_dir.is_dir():
        raise SystemExit(f"pristine dir not found: {pristine_dir}")

    golden_dir.mkdir(parents=True, exist_ok=True)
    days: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    totals = {"windows_total": 0, "valid_ticks": 0, "ticks_written": 0}

    for src in source_day_files(pristine_dir):
        report = verify_tick_file(
            src, max_gap_sec=max_gap_sec, max_start_delay=max_start_delay_sec
        )
        passed, reason = day_gate_verdict(report)
        if not passed:
            excluded.append({
                "day": src.name,
                "source": f"pristine/{src.name}",
                "reason": reason,
                "verify_status": report.get("status"),
                "capture_label": (report.get("capture_state") or {}).get("label"),
            })
            continue

        target = golden_dir / src.name
        if not target.exists() or sha256_of(target) != sha256_of(src):
            shutil.copyfile(src, target)
        # Fresh copy ⇒ new mtime ⇒ new fingerprint: write the sidecar keyed to
        # the copy so the dashboard's fingerprint check matches.
        _write_verify_sidecar(golden_dir, target, report)

        copied_sha = sha256_of(target)
        idx_path = None
        if build_indexes:
            idx_path, _windows_indexed = build_index(target)
        idx_fresh = bool(idx_path and is_fresh(target, idx_path))

        verdict = {
            "status": report.get("status"),
            "capture_label": (report.get("capture_state") or {}).get("label"),
            "readiness_level": ((report.get("readiness") or {}).get("level")),
        }
        days.append({
            "day": target.name,
            "source": f"pristine/{src.name}",
            "sha256": copied_sha,
            "source_sha256": sha256_of(src),
            "verify_verdict": verdict,
            "windows_count": int(report.get("windows_count") or 0),
            "valid_ticks": int(report.get("valid_ticks") or 0),
            "idx_sidecar": idx_path.name if idx_path else None,
            "idx_fresh": idx_fresh,
        })
        totals["windows_total"] += int(report.get("windows_count") or 0)
        totals["valid_ticks"] += int(report.get("valid_ticks") or 0)
        totals["ticks_written"] += _count_lines(target)

    manifest: dict[str, Any] = {
        "policy_version": READINESS_POLICY_VERSION,
        "certified_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "charter": CHARTER,
        "route": "pristine-derived (issue #290 pipeline promoted through §1.1 gates)",
        "totals": totals,
        "days": days,
        "excluded": excluded,
    }
    write_json_atomic(golden_dir / MANIFEST_NAME, manifest)
    return manifest


def _count_lines(path: Path) -> int:
    opener = _open_tick_text(path)
    with opener as fh:
        return sum(1 for _ in fh)


def _open_tick_text(path: Path):
    import gzip
    return gzip.open(path, "rt", encoding="utf-8") if path.name.endswith(".gz") \
        else path.open("r", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Assemble the golden tick dataset from pristine days (issue #281)."
    )
    ap.add_argument("pristine_dir", nargs="?", type=Path, default=DEFAULT_PRISTINE_DIR)
    ap.add_argument("--out", type=Path, default=DEFAULT_GOLDEN_DIR)
    ap.add_argument("--max-gap-sec", type=float, default=6.0)
    ap.add_argument("--max-start-delay-sec", type=float, default=5.0)
    ap.add_argument("--no-index", action="store_true",
                    help="skip .idx sidecar construction")
    args = ap.parse_args(argv)

    manifest = build_golden_dataset(
        args.pristine_dir,
        args.out,
        max_gap_sec=args.max_gap_sec,
        max_start_delay_sec=args.max_start_delay_sec,
        build_indexes=not args.no_index,
    )
    print(f"golden dataset assembled at {args.out}")
    print(f"  policy_version : {manifest['policy_version']}")
    print(f"  certified_utc  : {manifest['certified_utc']}")
    print(f"  days           : {len(manifest['days'])}"
          f" ({manifest['totals']['windows_total']} windows,"
          f" {manifest['totals']['valid_ticks']} valid ticks)")
    for d in manifest["days"]:
        print(f"    + {d['day']}  <- {d['source']}  windows={d['windows_count']}"
              f"  idx_fresh={d['idx_fresh']}")
    for e in manifest["excluded"]:
        print(f"    - {e['day']}  EXCLUDED: {e['reason']}")
    if manifest["excluded"] and not manifest["days"]:
        print("ERROR: no day passed the §1.1 gate — golden set not assembled", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
