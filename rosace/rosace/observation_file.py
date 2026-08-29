# SPDX-FileCopyrightText: 2026 The ROSACE project
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Observation persistence to disk, in YAML format.
Plays the same role for the YAML file that database.py plays for SQLite:
a module dedicated to persistence, separate from the orchestrator (main.py)
and from the pure data model (models.py).
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from .models import Observation

log = logging.getLogger("spectro.observation_file")


def create_observation_file(obs: Observation, target_dir: Path) -> Path:
    """
    Determines the observation's YAML filename (obs.suggested_filename) and
    stores it on obs.yaml_file — just the filename, not the full path: the
    directory is already carried separately by obs.obs_dir, and obs.yaml_file
    itself ends up inside the YAML file it names, where an absolute path
    would only have been a cumbersome repeat. The content is written by
    write_observation_file(), called right after by the caller.
    """
    filepath = target_dir / obs.suggested_filename
    obs.yaml_file = filepath.name
    log.info(f"[OBSERVATION_FILE] Observation file created: {obs.yaml_file}")
    return filepath


def write_observation_file(obs: Observation) -> None:
    """
    Rewrites the observation's YAML file with its full current state.
    Called after every stage (SCHEDULER → ARCHIVE): the file only ever
    grows/fills in, until it becomes the exhaustive record of the
    observation — useful after a crash to know where it got stuck.
    Does nothing until create_observation_file() has set obs.yaml_file.
    """
    if not obs.yaml_file:
        return
    content = obs.model_dump(mode="json")
    filepath = Path(obs.obs_dir) / obs.yaml_file
    with open(filepath, "w", encoding="utf-8") as f:
        yaml.dump(content, f, allow_unicode=True, default_flow_style=False, sort_keys=False)