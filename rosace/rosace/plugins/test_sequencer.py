"""
Tests for the SEQUENCER plugin (SequencerPlugin).
Pipeline loading (pipeline_loader.load_pipelines) is mocked: these tests
cover orchestration, not the real sub-plugins' behavior (covered by
sequencer_plugins/test_*.py).
Run: pytest plugins/test_sequencer.py -v
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
        obs.scheduler_data["program"] = "demo"
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
        with pytest.raises(RuntimeError, match="scheduler_data incomplete"):
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
            FakeSubPlugin("star_spectra_acquisition", calls),
        ]
        monkeypatch.setattr(
            "rosace.plugins.sequencer.load_pipelines",
            lambda program: (fake_pipeline, []),
        )

        obs = _make_obs()
        result = await SequencerPlugin().run(obs)

        assert calls == ["slew", "focus", "star_spectra_acquisition"]
        assert result.sequencer_data["order"] == ["slew", "focus", "star_spectra_acquisition"]

    async def test_sets_program_file_and_steps_including_sharing(self, monkeypatch):
        """obs.program_steps must list sequencer, reduction AND sharing —
        otherwise a reader of the observation's YAML can't know which SHARING
        sub-plugin was planned if the observation crashes before it runs."""
        monkeypatch.setattr(
            "rosace.plugins.sequencer.load_pipelines",
            lambda program: ([], []),
        )

        obs = _make_obs()  # program = "demo"
        result = await SequencerPlugin().run(obs)

        assert result.program_file == "demo.yaml"
        assert result.program_steps == {
            "sequencer": ["slew", "focus", "star_spectra_acquisition"],
            "reduction": ["bias", "dark", "flat", "extract"],
            "sharing": "blank_share",
        }

    async def test_loads_pipeline_for_observation_program(self, monkeypatch):
        seen_program = []

        def fake_load_pipelines(program):
            seen_program.append(program)
            return [], []

        monkeypatch.setattr("rosace.plugins.sequencer.load_pipelines", fake_load_pipelines)

        obs = _make_obs()
        obs.scheduler_data["program"] = "nova_monitoring"
        await SequencerPlugin().run(obs)

        assert seen_program == ["nova_monitoring"]

    async def test_subplugin_error_propagates(self, monkeypatch):
        calls: list[str] = []
        fake_pipeline = [
            FakeSubPlugin("slew", calls),
            FakeSubPlugin("focus", calls, raises=RuntimeError("focus failed")),
            FakeSubPlugin("star_spectra_acquisition", calls),
        ]
        monkeypatch.setattr(
            "rosace.plugins.sequencer.load_pipelines",
            lambda program: (fake_pipeline, []),
        )

        obs = _make_obs()
        with pytest.raises(RuntimeError, match="focus failed"):
            await SequencerPlugin().run(obs)

        assert calls == ["slew", "focus"]  # star_spectra_acquisition never reached
