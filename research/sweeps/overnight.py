"""Unattended re-analysis: wait for the capture to grow, then redo everything.

Run detached before going to bed. It waits until `--at`, rebuilds the window
cache from whatever the collector has by then, reruns the phase sweep and the
selection-bias null, and writes one report.

The point is not to run the same analysis again. It is that every number in it
is a function of sample size, and the current sample (36 binary legs for the
best config) is too small to separate edge from luck. Re-running on a larger
capture with the *same* code and the *same* grid makes the comparison honest:
anything that survives both is a candidate, anything that moves is noise.

    python -m research.sweeps.overnight --at 07:00
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SWEEPS = ROOT / "research" / "sweeps"
REPORT = SWEEPS / "overnight_report.md"
LOG = ROOT / "run" / "overnight.log"


def log(msg: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line)
    print(line, end="", flush=True)


def run(label: str, args: list[str], timeout: float = 7200) -> tuple[int, str]:
    log(f"START {label}")
    t0 = time.perf_counter()
    try:
        p = subprocess.run([sys.executable, *args], cwd=str(ROOT),
                           capture_output=True, text=True, timeout=timeout)
        out = (p.stdout or "") + (p.stderr or "")
        log(f"DONE  {label} rc={p.returncode} in {time.perf_counter() - t0:.0f}s")
        return p.returncode, out
    except subprocess.TimeoutExpired:
        log(f"TIMEOUT {label} after {timeout:.0f}s")
        return 124, f"{label} timed out after {timeout:.0f}s"
    except Exception as e:
        log(f"ERROR {label}: {type(e).__name__}: {e}")
        return 1, f"{label} raised {type(e).__name__}: {e}"


def dataset_size() -> dict:
    d = ROOT / "run" / "ticks"
    files = sorted(d.glob("ticks_*.jsonl"))
    return {"files": [f.name for f in files],
            "bytes": sum(f.stat().st_size for f in files)}


def tail(text: str, n: int = 40) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines[-n:])


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", default="07:00", help="local HH:MM to start")
    ap.add_argument("--perms", type=int, default=2000)
    ap.add_argument("--now", action="store_true", help="skip the wait")
    a = ap.parse_args(argv)

    if not a.now:
        hh, mm = (int(x) for x in a.at.split(":"))
        target = datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0)
        if target <= datetime.now():
            target += timedelta(days=1)
        log(f"overnight job armed for {target:%Y-%m-%d %H:%M}")
        while datetime.now() < target:
            time.sleep(30)

    before = dataset_size()
    log(f"starting; dataset {before['bytes'] / 1e9:.2f}GB across {len(before['files'])} files")

    sections: list[str] = []

    rc, out = run("cache rebuild", [str(SWEEPS / "ev_lab.py"), "cache", "--force"],
                  timeout=3600)
    sections.append(("## Cache rebuild\n\n```\n" + tail(out, 8) + "\n```"))
    if rc != 0:
        REPORT.write_text("# Overnight run FAILED at the cache rebuild\n\n"
                          + sections[-1], encoding="utf-8")
        log("aborting: cache rebuild failed")
        return 1

    for phase in ("phase1_1d", "phase4_universe", "phase5_band"):
        rc, out = run(phase, [str(SWEEPS / f"{phase}.py")], timeout=7200)
        body = tail(out, 26) if rc == 0 else out[-2000:]
        sections.append(f"## {phase} (rc={rc})\n\n```\n{body}\n```")

    rc, out = run("selection bias", [str(SWEEPS / "selection_bias.py"),
                                     "--perms", str(a.perms)], timeout=10800)
    sections.append(f"## Selection-bias null (rc={rc})\n\n```\n{tail(out, 22)}\n```")

    after = dataset_size()
    verdict = ""
    sb = SWEEPS / "selection_bias.json"
    if rc != 0:
        # The 2026-09-15 run reported a "Verdict" built from the PREVIOUS
        # night's selection_bias.json after the step itself had failed -- a
        # stale number presented as a fresh result, which is worse than no
        # number at all.
        verdict = (
            "\n## Verdict\n\nNONE — the selection-bias step failed "
            f"(rc={rc}). Any `selection_bias.json` on disk is from an earlier "
            "run and is deliberately not reported here.\n")
    elif sb.exists():
        try:
            d = json.loads(sb.read_text(encoding="utf-8"))
            p = d.get("p_family_wise")
            verdict = (
                f"\n## Verdict\n\n"
                f"- best config: `{d.get('best_name')}` at "
                f"**{d.get('best_observed_usd', 0):+.2f}$** on "
                f"{d.get('best_binary_legs')} binary legs\n"
                f"- null best-of-{d.get('configs')} median: "
                f"**{d.get('null_median', 0):+.2f}$**, 95th pct "
                f"**{d.get('null_p95', 0):+.2f}$**\n"
                f"- **family-wise p = {p}**\n\n"
                + ("A config only clears the search itself once this p drops "
                   "below 0.05 AND the observed total beats the 95th "
                   "percentile of the null.\n"))
        except Exception as e:
            verdict = f"\n## Verdict\n\ncould not read selection_bias.json: {e}\n"

    REPORT.write_text(
        f"# Overnight re-analysis — {datetime.now():%Y-%m-%d %H:%M}\n\n"
        f"Dataset grew from **{before['bytes'] / 1e9:.2f}GB** to "
        f"**{after['bytes'] / 1e9:.2f}GB** "
        f"({len(after['files'])} day files).\n"
        + verdict + "\n" + "\n\n".join(sections) + "\n",
        encoding="utf-8")
    log(f"wrote {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
