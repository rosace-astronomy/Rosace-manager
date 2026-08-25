"""
Base class shared by every sub-plugin (sequencer and reduction).
Each sub-plugin inherits from SubPlugin and implements `execute()`.
"""

from __future__ import annotations

import logging

from ..models import Observation


class SubPlugin:
    """
    Base class for the sequencer and reduction sub-plugins.

    Each sub-plugin has:
    - a name (used in logs)
    - an `execute(obs: Observation) -> Observation` method to implement

    The sub-plugin reads/writes its parent stage plugin's casier
    (obs.sequencer_data or obs.reduction_data), and may read a previous
    step's if needed (e.g. bias reads obs.sequencer_data["star_spectra_images"]
    — a shared key, written under a fixed name independent of whichever
    SEQUENCER sub-plugin produced it, not obs.sequencer_data["<sub_plugin_name>"]:
    REDUCTION must never need to know that name).

    All configuration lives in conf/ (config.yaml, hardware_<variant>.yaml)
    like the rest of the application — no config.yaml of its own for a sub-plugin.
    """

    def __init__(self) -> None:
        self.name = self.__class__.__name__
        self.log = logging.getLogger(f"spectro.subplugin.{self.name.lower()}")

    async def execute(self, obs: Observation) -> Observation:
        """
        The sub-plugin's logic. Receives the current observation file,
        returns the enriched file.
        Any exception propagates to the parent plugin's orchestrator.
        """
        raise NotImplementedError(f"{self.name} must implement execute()")
