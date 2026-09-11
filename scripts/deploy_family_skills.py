#!/usr/bin/env python3
"""Deploy the 7-skill issue workflow family across all agent harnesses via Directory Junctions.

Canonical source of truth is: ~/.agents/skills/<skill-name>
Harness targets:
  - ~/.claude/skills/<skill-name>
  - ~/.gemini/config/skills/<skill-name>
  - ~/AppData/Local/hermes/skills/<skill-name>

Usage:
    python scripts/deploy_family_skills.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path(os.environ.get("USERPROFILE", os.path.expanduser("~")))
CANONICAL_ROOT = HOME / ".agents" / "skills"

FAMILY_SKILLS = [
    "create-issue",
    "plan-issue",
    "build-plan",
    "review-build-and-pr",
    "explain-issue",
    "workflow-issue",
    "babysit-pr-and-merge",
]

# Every retired skill name across the family's history. Each entry is removed
# from all harness roots on every run so stale/dangling links never linger.
LEGACY_NAMES = [
    "issue-create",       # original name of create-issue
    "pr-babysitter",      # original name of review-babysitter
    "work-issue",         # pre-rename name of workflow-issue
    "build-issue",        # pre-rename name of build-plan
    "ship-issue",         # pre-rename name of review-build-and-pr
    "review-babysitter",  # pre-rename name of babysit-pr-and-merge
]

HARNESS_ROOTS = [
    HOME / ".claude" / "skills",
    HOME / ".gemini" / "config" / "skills",
    HOME / "AppData" / "Local" / "hermes" / "skills",
]


def clean_legacy_junctions() -> None:
    """Remove legacy junction points or directories from all harness skill roots."""
    for root in HARNESS_ROOTS:
        if not root.exists():
            continue
        for legacy in LEGACY_NAMES:
            target = root / legacy
            # exists() is False for a dangling junction/symlink, so also check
            # is_symlink() and the raw lexists on the path entry itself.
            entry_exists = target.exists() or target.is_symlink() or os.path.lexists(target)
            if entry_exists:
                print(f"[CLEAN] Removing legacy entry: {target}")
                try:
                    # If directory junction, rmdir works; if real directory, shutil.rmtree
                    res = subprocess.run(["cmd", "/c", "rmdir", str(target)], capture_output=True)
                    if res.returncode != 0:
                        if target.is_dir():
                            shutil.rmtree(target)
                        else:
                            target.unlink()
                except Exception as e:
                    print(f"        Warning: failed to remove {target}: {e}")


def create_junction(src: Path, dst: Path) -> bool:
    """Create a Windows directory junction from src to dst via mklink /J."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        try:
            if os.path.samefile(src, dst):
                return True
        except OSError:
            pass
        # Safely remove existing target directory or junction
        try:
            res = subprocess.run(["cmd", "/c", "rmdir", str(dst)], capture_output=True)
            if res.returncode != 0:
                if dst.is_dir():
                    shutil.rmtree(dst)
                else:
                    dst.unlink()
        except Exception as e:
            print(f"  [ERR] Failed removing existing {dst}: {e}")
            return False

    # Create directory junction via mklink /J
    res = subprocess.run(["cmd", "/c", "mklink", "/J", str(dst), str(src)], capture_output=True, text=True)
    if res.returncode != 0:
        print(f"  [ERR] mklink /J failed: {res.stderr.strip()}")
        return False
    return True


def deploy_family() -> int:
    """Deploy the 7-skill issue workflow family across all harnesses and verify integrity."""
    print("==================================================")
    print(" Deploying 7-Skill Issue Workflow Family")
    print(f" Canonical Root: {CANONICAL_ROOT}")
    print("==================================================")

    # 1. Verify canonical sources exist
    for skill in FAMILY_SKILLS:
        skill_path = CANONICAL_ROOT / skill
        skill_md = skill_path / "SKILL.md"
        if not skill_md.is_file():
            print(f"[FATAL] Missing canonical SKILL.md for {skill}: {skill_md}")
            return 1

    # 2. Clean legacy junctions
    clean_legacy_junctions()

    # 3. Create junctions for all 7 skills across harnesses
    errors = 0
    for root in HARNESS_ROOTS:
        harness_name = root.parent.name if root.name == "skills" else root.name
        print(f"\nTarget Harness: {root} ({harness_name})")
        for skill in FAMILY_SKILLS:
            src = CANONICAL_ROOT / skill
            dst = root / skill
            ok = create_junction(src, dst)
            status = "[OK]" if ok else "[FAIL]"
            print(f"  {status} {skill} -> {dst}")
            if not ok:
                errors += 1

    # 4. Verify resolution
    print("\nVerifying live file resolution across all junctions:")
    for root in HARNESS_ROOTS:
        for skill in FAMILY_SKILLS:
            check_md = root / skill / "SKILL.md"
            check_ref = root / skill / "reference.md"
            if check_md.is_file() and check_ref.is_file():
                print(f"  [VERIFIED] {skill} in {root.name}: SKILL.md ({check_md.stat().st_size}b), reference.md ({check_ref.stat().st_size}b)")
            else:
                if not check_md.is_file():
                    print(f"  [BROKEN] Missing {check_md}")
                if not check_ref.is_file():
                    print(f"  [BROKEN] Missing {check_ref}")
                errors += 1

    if errors == 0:
        print("\n[SUCCESS] All 7 skills successfully deployed and verified via junctions.")
        return 0
    else:
        print(f"\n[ERROR] Deployment finished with {errors} errors.")
        return 1


if __name__ == "__main__":
    sys.exit(deploy_family())
