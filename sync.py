#!/usr/bin/env python3
"""Simple OrcaSlicer profile sync CLI.

This script keeps a Git-tracked mirror of your OrcaSlicer files in this repo and
syncs between your local OrcaSlicer directory and that mirror.

Storage model inside this repository:
- ./profiles/               -> Git-tracked mirror copy of OrcaSlicer files
- ./.orcasync/state.json    -> Last-sync file-hash baseline for conflict detection
- ./.orcasync/config.json   -> Local settings (OrcaSlicer path + mirror path)

Commands:
- python3 sync.py status
- python3 sync.py push [-m "message"]
- python3 sync.py pull
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Set

# Resolve repository root as the directory containing this script.
REPO_ROOT = Path(__file__).resolve().parent

# All tool-owned files are placed under .orcasync so they are easy to find.
APP_DIR = REPO_ROOT / ".orcasync"
CONFIG_PATH = APP_DIR / "config.json"
STATE_PATH = APP_DIR / "state.json"

# Default mirror directory that is committed to GitHub.
DEFAULT_MIRROR_DIR = REPO_ROOT / "profiles"


@dataclass
class ThreeWayDiff:
    """Three-way comparison used for sync conflict detection.

    local_hashes: hashes from local OrcaSlicer directory
    mirror_hashes: hashes from repo mirror directory (./profiles)
    base_hashes: hashes from last successful sync (.orcasync/state.json)
    """

    only_local: List[str]
    only_mirror: List[str]
    changed_local: List[str]
    changed_mirror: List[str]
    conflicts: List[str]


def detect_default_orca_path() -> str:
    """Return a best-effort default OrcaSlicer path by OS."""
    system = platform.system().lower()
    if "darwin" in system:
        return "~/Library/Application Support/OrcaSlicer"
    if "windows" in system:
        return "%APPDATA%\\OrcaSlicer"
    return "~/.config/OrcaSlicer"


def expand_path(raw: str) -> Path:
    """Expand ~, $VAR, and %VAR% syntax into an absolute path.

    We handle %VAR% manually so Windows-style config strings also work on macOS.
    """
    normalized = raw
    # Expand %VARNAME% placeholders.
    for key, value in os.environ.items():
        normalized = normalized.replace(f"%{key}%", value)
    # Expand Unix-style variables and user home.
    expanded = os.path.expandvars(os.path.expanduser(normalized))
    return Path(expanded).resolve()


def ensure_dirs() -> None:
    """Create tool-owned directories/files if missing."""
    APP_DIR.mkdir(parents=True, exist_ok=True)


def write_default_config_if_missing() -> None:
    """Create a first-run config with safe defaults.

    This keeps setup quick and avoids manual boilerplate for MVP use.
    """
    if CONFIG_PATH.exists():
        return

    cfg = {
        # Local live OrcaSlicer directory this machine reads/writes.
        "local_orca_dir": detect_default_orca_path(),
        # Mirror directory inside this repo that will be committed/pushed.
        "repo_mirror_dir": "./profiles",
        # File/dir patterns to ignore from syncing.
        "exclude_substrings": [
            "/cache/",
            "/Cache/",
            "/logs/",
            "/Logs/",
            ".DS_Store",
            "Thumbs.db",
            ".lock",
        ],
    }
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def load_config() -> dict:
    """Load configuration and resolve paths to absolute values."""
    ensure_dirs()
    write_default_config_if_missing()

    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    local_dir = expand_path(cfg["local_orca_dir"])
    mirror_raw = cfg.get("repo_mirror_dir", "./profiles")

    # Support either relative repo path or absolute override.
    mirror_dir = Path(mirror_raw)
    if not mirror_dir.is_absolute():
        mirror_dir = (REPO_ROOT / mirror_dir).resolve()

    cfg["_local_dir_resolved"] = local_dir
    cfg["_mirror_dir_resolved"] = mirror_dir
    cfg.setdefault("exclude_substrings", [])
    return cfg


def load_state() -> Dict[str, str]:
    """Read last-sync baseline hashes from .orcasync/state.json."""
    if not STATE_PATH.exists():
        return {}
    payload = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return payload.get("hashes", {}) if isinstance(payload, dict) else {}


def save_state(hashes: Dict[str, str]) -> None:
    """Persist a new baseline after a successful non-conflicting sync."""
    ensure_dirs()
    payload = {"hashes": hashes}
    STATE_PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def is_excluded(rel_posix: str, exclude_substrings: List[str]) -> bool:
    """Exclude files by substring match for a lightweight MVP filter."""
    return any(x in rel_posix for x in exclude_substrings)


def sha256_file(path: Path) -> str:
    """Hash a file in chunks for stable content comparisons."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_hashes(root: Path, exclude_substrings: List[str]) -> Dict[str, str]:
    """Return {relative_posix_path: sha256} for all files under root."""
    result: Dict[str, str] = {}
    if not root.exists():
        return result

    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if is_excluded(rel, exclude_substrings):
            continue
        result[rel] = sha256_file(p)
    return result


def compute_three_way(local: Dict[str, str], mirror: Dict[str, str], base: Dict[str, str]) -> ThreeWayDiff:
    """Compute a conflict-aware 3-way diff.

    Conflict definition:
    - File hash differs from base on local AND differs from base on mirror
    - And local hash != mirror hash
    """
    all_paths: Set[str] = set(local) | set(mirror) | set(base)

    only_local: List[str] = []
    only_mirror: List[str] = []
    changed_local: List[str] = []
    changed_mirror: List[str] = []
    conflicts: List[str] = []

    for rel in sorted(all_paths):
        l = local.get(rel)
        m = mirror.get(rel)
        b = base.get(rel)

        local_changed = l != b
        mirror_changed = m != b

        if local_changed and mirror_changed and l != m:
            conflicts.append(rel)
            continue

        # Track presence-only differences for status output.
        if l is not None and m is None:
            only_local.append(rel)
        elif l is None and m is not None:
            only_mirror.append(rel)

        # Track one-sided modifications relative to baseline.
        if local_changed and not mirror_changed:
            changed_local.append(rel)
        elif mirror_changed and not local_changed:
            changed_mirror.append(rel)

    return ThreeWayDiff(
        only_local=only_local,
        only_mirror=only_mirror,
        changed_local=changed_local,
        changed_mirror=changed_mirror,
        conflicts=conflicts,
    )


def ensure_parent(path: Path) -> None:
    """Ensure destination parent directories exist before copying."""
    path.parent.mkdir(parents=True, exist_ok=True)


def copy_file(src_root: Path, dst_root: Path, rel: str) -> None:
    """Copy one relative file path from src tree to dst tree."""
    src = src_root / rel
    dst = dst_root / rel
    ensure_parent(dst)
    shutil.copy2(src, dst)


def remove_file(root: Path, rel: str) -> None:
    """Delete one relative file and prune empty parent directories."""
    target = root / rel
    if target.exists():
        target.unlink()
    parent = target.parent
    while parent != root and parent.exists():
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def run_git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run git in this repository and capture output for clear errors."""
    cmd = ["git", "-C", str(REPO_ROOT), *args]
    return subprocess.run(cmd, check=check, text=True, capture_output=True)


def git_commit_if_needed(message: str) -> bool:
    """Commit staged/untracked changes if there is anything to commit."""
    status = run_git("status", "--porcelain")
    if not status.stdout.strip():
        print("No git changes to commit.")
        return False

    run_git("add", "-A")
    run_git("commit", "-m", message)
    print(f"Committed: {message}")
    return True


def print_storage_locations(cfg: dict) -> None:
    """Print exactly where each data category is stored.

    This is intentionally verbose so users always know where data lives.
    """
    print("Storage locations:")
    print(f"  Local OrcaSlicer live data: {cfg['_local_dir_resolved']}")
    print(f"  Repo mirror (Git-tracked):  {cfg['_mirror_dir_resolved']}")
    print(f"  Sync baseline state:         {STATE_PATH}")
    print(f"  Tool config:                 {CONFIG_PATH}")


def cmd_status(cfg: dict) -> int:
    """Show differences and conflicts without changing anything."""
    local_dir: Path = cfg["_local_dir_resolved"]
    mirror_dir: Path = cfg["_mirror_dir_resolved"]
    excludes: List[str] = cfg["exclude_substrings"]

    local_hashes = collect_hashes(local_dir, excludes)
    mirror_hashes = collect_hashes(mirror_dir, excludes)
    base_hashes = load_state()

    diff = compute_three_way(local_hashes, mirror_hashes, base_hashes)

    print_storage_locations(cfg)
    print("\nStatus summary:")
    print(f"  local-only files:   {len(diff.only_local)}")
    print(f"  mirror-only files:  {len(diff.only_mirror)}")
    print(f"  local changes:      {len(diff.changed_local)}")
    print(f"  mirror changes:     {len(diff.changed_mirror)}")
    print(f"  conflicts:          {len(diff.conflicts)}")

    if diff.conflicts:
        print("\nConflicts (manual resolution required):")
        for rel in diff.conflicts[:25]:
            print(f"  - {rel}")
        if len(diff.conflicts) > 25:
            print(f"  ... and {len(diff.conflicts) - 25} more")

    return 1 if diff.conflicts else 0


def cmd_push(cfg: dict, message: str) -> int:
    """Copy local -> mirror, then commit + push to GitHub.

    Push is blocked if conflicts are detected so we never overwrite divergent
    edits silently.
    """
    local_dir: Path = cfg["_local_dir_resolved"]
    mirror_dir: Path = cfg["_mirror_dir_resolved"]
    excludes: List[str] = cfg["exclude_substrings"]

    if not local_dir.exists():
        print(f"Local OrcaSlicer directory does not exist: {local_dir}")
        return 2

    mirror_dir.mkdir(parents=True, exist_ok=True)

    local_hashes = collect_hashes(local_dir, excludes)
    mirror_hashes = collect_hashes(mirror_dir, excludes)
    base_hashes = load_state()
    diff = compute_three_way(local_hashes, mirror_hashes, base_hashes)

    print_storage_locations(cfg)

    if diff.conflicts:
        print("\nPush blocked due to conflicts:")
        for rel in diff.conflicts[:25]:
            print(f"  - {rel}")
        print("Resolve manually, then rerun push.")
        return 1

    # Apply local truth to mirror for changed/added/removed paths.
    to_copy = sorted(set(diff.only_local + diff.changed_local))
    to_remove = sorted(set(diff.only_mirror + diff.changed_mirror))

    for rel in to_copy:
        copy_file(local_dir, mirror_dir, rel)
    for rel in to_remove:
        remove_file(mirror_dir, rel)

    # Recompute mirror hashes after write and persist as the new baseline.
    new_hashes = collect_hashes(mirror_dir, excludes)
    save_state(new_hashes)

    # Commit and push all resulting changes (mirror files + state file).
    try:
        committed = git_commit_if_needed(message)
        if committed:
            run_git("push")
            print("Pushed to remote.")
    except subprocess.CalledProcessError as e:
        print(e.stderr or e.stdout)
        return e.returncode or 1

    return 0


def cmd_pull(cfg: dict) -> int:
    """Pull latest git changes, then copy mirror -> local.

    Pull is blocked if conflicts are detected so we avoid clobbering local edits.
    """
    local_dir: Path = cfg["_local_dir_resolved"]
    mirror_dir: Path = cfg["_mirror_dir_resolved"]
    excludes: List[str] = cfg["exclude_substrings"]

    # Bring repo mirror up to date first.
    try:
        run_git("pull", "--rebase")
    except subprocess.CalledProcessError as e:
        print(e.stderr or e.stdout)
        return e.returncode or 1

    local_hashes = collect_hashes(local_dir, excludes)
    mirror_hashes = collect_hashes(mirror_dir, excludes)
    base_hashes = load_state()
    diff = compute_three_way(local_hashes, mirror_hashes, base_hashes)

    print_storage_locations(cfg)

    if diff.conflicts:
        print("\nPull blocked due to conflicts:")
        for rel in diff.conflicts[:25]:
            print(f"  - {rel}")
        print("Resolve manually, then rerun pull.")
        return 1

    # Apply mirror truth to local for changed/added/removed paths.
    local_dir.mkdir(parents=True, exist_ok=True)
    to_copy = sorted(set(diff.only_mirror + diff.changed_mirror))
    to_remove = sorted(set(diff.only_local + diff.changed_local))

    for rel in to_copy:
        copy_file(mirror_dir, local_dir, rel)
    for rel in to_remove:
        remove_file(local_dir, rel)

    # Baseline should match the mirror after a successful pull apply.
    new_hashes = collect_hashes(mirror_dir, excludes)
    save_state(new_hashes)

    print("Pull sync applied to local OrcaSlicer directory.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Construct CLI argument parser."""
    p = argparse.ArgumentParser(description="Simple OrcaSlicer Git sync")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show differences and conflicts")

    push = sub.add_parser("push", help="Sync local -> repo mirror, then git commit/push")
    push.add_argument(
        "-m",
        "--message",
        default="Sync OrcaSlicer profiles",
        help="Git commit message for push",
    )

    sub.add_parser("pull", help="git pull, then sync repo mirror -> local")

    return p


def main() -> int:
    """Entry point: parse command and dispatch."""
    args = build_parser().parse_args()
    cfg = load_config()

    if args.command == "status":
        return cmd_status(cfg)
    if args.command == "push":
        return cmd_push(cfg, args.message)
    if args.command == "pull":
        return cmd_pull(cfg)

    return 2


if __name__ == "__main__":
    sys.exit(main())
