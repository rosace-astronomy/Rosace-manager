# SPDX-FileCopyrightText: 2026 The ROSACE project
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Purge of the demo, to start a fresh one: everything under the demo
configuration's base_data_dir (databases, log, observations, dark library,
GUI previews, observation list, reduction reference files) except the replay
dataset -- the hardware profile's replay_dataset_dir, a link or a real
directory. At the next start of the demo server, demo_seed.py creates the
account, catalogue, observation list and previews again.

Refused unless the configuration is the demo one (is_demo: true), and while
a demo server runs (its fake PHD2 answers on the replay profile's port):
the server holds the databases open.

Used by `python -m rosace.main --purge-demo [--apply]`, which points
ROSACE_CONFIG at the packaged conf/config_demo.yaml first.
"""

from __future__ import annotations

import shutil
import socket
from dataclasses import dataclass
from pathlib import Path

import yaml

from .config import BASE_DATA_DIR, HARDWARE_CONFIG_PATH, IS_DEMO_MODE
from .hardware_replay.bench import dataset_dir_from_profile


@dataclass
class DemoEntry:
    path: Path
    files: int
    size: int


def _measure(path: Path) -> tuple[int, int]:
    """(files, bytes) under path, links not followed."""
    if path.is_symlink() or path.is_file():
        return 1, path.lstat().st_size
    files = size = 0
    for item in path.rglob("*"):
        if item.is_symlink() or item.is_file():
            files += 1
            size += item.lstat().st_size
    return files, size


def demo_entries(base_dir: Path, keep: Path | None) -> list[DemoEntry]:
    """Every top-level entry of base_dir but keep (the replay dataset)."""
    if not base_dir.is_dir():
        return []
    kept = keep.expanduser() if keep is not None else None
    entries = []
    for path in sorted(base_dir.iterdir()):
        if kept is not None and path == kept:
            continue
        entries.append(DemoEntry(path, *_measure(path)))
    return entries


def demo_server_running(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def remove(entries: list[DemoEntry]) -> None:
    for entry in entries:
        if entry.path.is_dir() and not entry.path.is_symlink():
            shutil.rmtree(entry.path)
        else:
            entry.path.unlink()


def report(entries: list[DemoEntry]) -> str:
    lines = [f"  {e.path}  ({e.files} files, {e.size / 1e6:.1f} MB)" for e in entries]
    total_files, total_size = sum(e.files for e in entries), sum(e.size for e in entries)
    lines.append(f"Total: {len(entries)} entries, {total_files} files, {total_size / 1e6:.1f} MB")
    return "\n".join(lines)


def purge_demo(apply: bool = False) -> tuple[list[DemoEntry], Path | None]:
    """The demo entries to remove, removed when apply is True, and the kept
    replay dataset. Raises RuntimeError when the configuration is not the
    demo one or a demo server runs."""
    if not IS_DEMO_MODE:
        raise RuntimeError("not the demo configuration (is_demo: true missing) -- nothing purged")
    with open(HARDWARE_CONFIG_PATH, encoding="utf-8") as f:
        phd2 = (yaml.safe_load(f) or {}).get("PHD2_config", {})
    if phd2 and demo_server_running(phd2.get("host", "127.0.0.1"), int(phd2.get("port", 4499))):
        raise RuntimeError("a demo server is running (its fake PHD2 answers) -- stop it first")
    keep = dataset_dir_from_profile()
    entries = demo_entries(BASE_DATA_DIR, keep)
    if apply:
        remove(entries)
    return entries, keep
