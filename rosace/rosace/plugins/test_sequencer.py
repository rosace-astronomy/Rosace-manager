"""
Tests du plugin SEQUENCER (SequencerPlugin).
Le chargement du pipeline (pipeline_loader.load_pipelines) est mocké : ces
tests couvrent l'orchestration, pas le comportement des vrais sous-plugins
(couvert par sequencer_plugins/test_*.py).
Lancement : pytest plugins/test_sequencer.py -v
"""

from datetime import datetime, timezone

import pytest

from rosace.models import Observation, Target, ExposureConfig
from rosace.plugins.sequencer import SequencerPlugin


def _make_obs(with_scheduler_data=True, obs_dir="/tmp/whatever") -> Observation:
    obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234", obs_dir=obs_dir)
    if with_scheduler_data:
        obs.scheduler_data["target"] = Target(name="Vega", ra=279.23, dec=38.78)
        obs.scheduler_data["exposure"] = ExposureConfig(exposure_time=10.0)
        obs.scheduler_data["programme"] = "etoiles_be"
    return obs


class FakeSubPlugin:
    def __init__(self, name, calls, raises=None):
        self.name = name
        self.calls = calls
        self.raises = raises

    async def execute(self, obs: Observation) -> Observation:
        self.calls.append(self.name)
        if self.raises:
            raise self.raises
        obs.sequencer_data.setdefault("order", []).append(self.name)
        return obs


class TestSequencerPlugin:

    async def test_raises_if_scheduler_data_incomplete(self):
        obs = _make_obs(with_scheduler_data=False)
        with pytest.raises(RuntimeError, match="scheduler_data incomplet"):
            await SequencerPlugin().run(obs)

    async def test_raises_if_obs_dir_missing(self):
        obs = _make_obs(obs_dir=None)
        with pytest.raises(RuntimeError, match="obs_dir"):
            await SequencerPlugin().run(obs)

    async def test_calls_subplugins_in_order(self, monkeypatch):
        calls: list[str] = []
        fake_pipeline = [
            FakeSubPlugin("slew", calls),
            FakeSubPlugin("focus", calls),
            FakeSubPlugin("expose", calls),
        ]
        monkeypatch.setattr(
            "rosace.plugins.sequencer.load_pipelines",
            lambda programme: (fake_pipeline, []),
        )

        obs = _make_obs()
        result = await SequencerPlugin().run(obs)

        assert calls == ["slew", "focus", "expose"]
        assert result.sequencer_data["order"] == ["slew", "focus", "expose"]

    async def test_loads_pipeline_for_observation_programme(self, monkeypatch):
        seen_programme = []

        def fake_load_pipelines(programme):
            seen_programme.append(programme)
            return [], []

        monkeypatch.setattr("rosace.plugins.sequencer.load_pipelines", fake_load_pipelines)

        obs = _make_obs()
        obs.scheduler_data["programme"] = "nova_monitoring"
        await SequencerPlugin().run(obs)

        assert seen_programme == ["nova_monitoring"]

    async def test_subplugin_error_propagates(self, monkeypatch):
        calls: list[str] = []
        fake_pipeline = [
            FakeSubPlugin("slew", calls),
            FakeSubPlugin("focus", calls, raises=RuntimeError("focus failed")),
            FakeSubPlugin("expose", calls),
        ]
        monkeypatch.setattr(
            "rosace.plugins.sequencer.load_pipelines",
            lambda programme: (fake_pipeline, []),
        )

        obs = _make_obs()
        with pytest.raises(RuntimeError, match="focus failed"):
            await SequencerPlugin().run(obs)

        assert calls == ["slew", "focus"]  # expose jamais atteint
