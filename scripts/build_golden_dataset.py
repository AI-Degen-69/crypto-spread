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
import hashlib
import json
import os
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
    except OSError:
        # Best-effort cache write; a failure here is logged, never swallowed —
        # the manifest carries the authoritative verdict either way.
        print(f"warn: verify sidecar write failed for {target.name}", file=sys.stderr)


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


def day_key_from_name(name: str) -> str:
    """Charter §3.1: `day` is the UTC day key ('2026-09-13'), not a filename."""
    stem = name
    for suffix in DAY_SUFFIXES:
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    import re

    for token in reversed(re.findall(r"\d{4}-\d{2}-\d{2}", stem)):
        try:
            datetime.strptime(token, "%Y-%m-%d")
            return token
        except ValueError:
            continue
    return stem


def charter_set_totals(days: list[dict[str, Any]]) -> dict[str, Any]:
    """Set-level totals in the charter §3.1 vocabulary."""
    windows_total = sum(int(d["windows_count"]) for d in days)
    gaps = sum(int(d.get("sampling_gaps_count") or 0) for d in days)
    blocks: set[str] = set()
    for d in days:
        blocks.update(d.get("time_blocks") or [])
    return {
        "windows_count": windows_total,
        "valid_ticks": sum(int(d["valid_ticks"]) for d in days),
        "time_blocks": sorted(blocks),
        "sampling_gap_rate": round(gaps / windows_total, 6) if windows_total else 0.0,
    }


def charter_set_gates(days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Charter §1.2 — the set-level targets, evaluated over the assembled days.

    Mirrors the dashboard card's targets (both derive from the charter); kept
    local so the builder never imports the FastAPI app.
    """
    from strategy.series import SERIES

    totals = charter_set_totals(days)
    pair_windows: dict[str, int] = {}
    for d in days:
        for m in d.get("market_breakdown") or []:
            series = m.get("series", "")
            pair_windows[series] = pair_windows.get(series, 0) + int(m.get("windows") or 0)
    for series, _dur, _label in SERIES:
        pair_windows.setdefault(series, 0)
    weakest = min(pair_windows.values(), default=0)
    present = sum(1 for s, _d, _l in SERIES if pair_windows.get(s, 0) > 0)
    windows_total = totals["windows_count"]
    return [
        {"name": "windows_count", "measured": windows_total, "required": 500,
         "ok": windows_total >= 500},
        {"name": "windows_per_market_pair", "measured": weakest, "required": 50,
         "ok": weakest >= 50},
        {"name": "time_blocks", "measured": len(totals["time_blocks"]), "required": 5,
         "ok": len(totals["time_blocks"]) >= 5},
        {"name": "valid_ticks", "measured": totals["valid_ticks"], "required": 50_000,
         "ok": totals["valid_ticks"] >= 50_000},
        {"name": "sampling_gap_rate", "measured": totals["sampling_gap_rate"],
         "required": 0.05, "ok": totals["sampling_gap_rate"] <= 0.05},
        {"name": "all_10_series_present", "measured": present, "required": len(SERIES),
         "ok": present == len(SERIES)},
    ]


def inputs_identity(
    policy_version: str,
    days: list[dict[str, Any]],
    excluded: list[dict[str, Any]],
) -> str:
    """Content hash of everything the certification claims about the inputs."""
    payload = json.dumps(
        {
            "policy_version": policy_version,
            "days": [
                {k: d[k] for k in ("day", "file", "source", "source_sha256", "sha256")}
                for d in days
            ],
            "excluded": sorted(f"{e['day']}|{e['reason']}" for e in excluded),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _certification_date(prev_manifest: dict[str, Any], identity: str) -> str:
    """Byte-identical manifest across re-runs: reuse the recorded certification
    date while the input identity is unchanged; recertify (new date) otherwise."""
    if prev_manifest.get("inputs_sha256") == identity and prev_manifest.get("certified_utc"):
        return str(prev_manifest["certified_utc"])
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


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

    # Previous certification, if any — used to keep certified_utc stable for
    # unchanged inputs (byte-identical manifest across re-runs on any date).
    prev_manifest: dict[str, Any] = {}
    try:
        loaded_prev = json.loads((golden_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
        if isinstance(loaded_prev, dict):
            prev_manifest = loaded_prev
    except Exception:
        prev_manifest = {}

    days: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    for src in source_day_files(pristine_dir):
        report = verify_tick_file(
            src, max_gap_sec=max_gap_sec, max_start_delay=max_start_delay_sec
        )
        passed, reason = day_gate_verdict(report)
        if not passed:
            excluded.append({
                "day": day_key_from_name(src.name),
                "file": src.name,
                "source": f"pristine/{src.name}",
                "reason": reason,
                "verify_status": report.get("status"),
                "capture_label": (report.get("capture_state") or {}).get("label"),
            })
            continue

        target = golden_dir / src.name
        if not target.exists() or sha256_of(target) != sha256_of(src):
            # Atomic promotion: copy to a temp file in the same directory and
            # replace the target only after the copy succeeds — a failed build
            # never leaves a truncated golden day, and a previously certified
            # day survives the failure intact.
            tmp_target = target.with_name(f".{target.name}.tmp")
            try:
                shutil.copyfile(src, tmp_target)
                os.replace(tmp_target, target)
            except BaseException:
                tmp_target.unlink(missing_ok=True)
                raise
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
            "day": day_key_from_name(src.name),
            "file": target.name,
            "source": f"pristine/{src.name}",
            "sha256": copied_sha,
            "source_sha256": sha256_of(src),
            "verify_verdict": verdict,
            "windows_count": int(report.get("windows_count") or 0),
            "valid_ticks": int(report.get("valid_ticks") or 0),
            "sampling_gaps_count": int(report.get("sampling_gaps_count") or 0),
            "time_blocks": list(report.get("time_blocks") or []),
            "market_breakdown": list(report.get("market_breakdown") or []),
            "idx_sidecar": idx_path.name if idx_path else None,
            "idx_fresh": idx_fresh,
        })

    # Reconcile (§3.1): the golden directory is the published set — a day that
    # no longer passes §1.1 or vanished from the source is removed here, never
    # silently kept over from an earlier build.
    passing_files = {d["file"] for d in days}
    removed: list[dict[str, Any]] = []
    for stale in sorted(golden_dir.iterdir()):
        if not (stale.is_file() and stale.name.endswith(DAY_SUFFIXES)):
            continue
        if stale.name in passing_files:
            continue
        stale.unlink()
        removed.append({"day": stale.name, "reason": "no longer passes §1.1 / absent from source"})
        for extra in (
            golden_dir.parent / VERIFY_CACHE_DIRNAME / golden_dir.name / f"{stale.name}.json",
            stale.with_name(stale.name + ".idx"),
        ):
            try:
                extra.unlink()
            except OSError:
                pass

    totals = charter_set_totals(days)
    gates = charter_set_gates(days)
    certified = bool(days) and all(g["ok"] for g in gates)

    identity = inputs_identity(READINESS_POLICY_VERSION, days, excluded)
    manifest: dict[str, Any] = {
        "policy_version": READINESS_POLICY_VERSION,
        # Only a complete §1.2-passing set is certified; an incomplete assembly
        # is published explicitly uncertified (certified_utc stays null).
        "status": "certified" if certified else "incomplete",
        "certified_utc": _certification_date(prev_manifest, identity) if certified else None,
        "inputs_sha256": identity,
        "charter": CHARTER,
        "route": "pristine-derived (issue #290 pipeline promoted through §1.1 gates)",
        "totals": totals,
        "set_gates": gates,
        "days": days,
        "excluded": excluded,
        "removed_stale": removed,
    }
    write_json_atomic(golden_dir / MANIFEST_NAME, manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    """CLI entry: assemble golden/ from a pristine dir and print the certification summary."""
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
    print(f"  status         : {manifest['status']}")
    print(f"  certified_utc  : {manifest['certified_utc']}")
    print(f"  days           : {len(manifest['days'])}"
          f" ({manifest['totals']['windows_count']} windows,"
          f" {manifest['totals']['valid_ticks']} valid ticks)")
    for d in manifest["days"]:
        print(f"    + {d['day']}  <- {d['source']}  windows={d['windows_count']}"
              f"  idx_fresh={d['idx_fresh']}")
    for e in manifest["excluded"]:
        print(f"    - {e['day']}  EXCLUDED: {e['reason']}")
    for r in manifest["removed_stale"]:
        print(f"    ~ {r['day']}  REMOVED STALE: {r['reason']}")
    for g in manifest["set_gates"]:
        mark = "ok  " if g["ok"] else "MISS"
        print(f"    [{mark}] {g['name']}: {g['measured']} (need {g['required']})")
    if manifest["status"] != "certified":
        print("ERROR: assembled set does not meet charter §1.2 — published as "
              "'incomplete', not certified", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
