"""
Tests for the REDUCTION plugin (ReductionPlugin).
Pipeline loading (pipeline_loader.load_pipelines) is mocked: these tests
cover orchestration, not the real sub-plugins' behavior (covered by
reduction_plugins/test_*.py).
Run: pytest plugins/test_reduction.py -v
"""

from datetime import datetime, timezone

import pytest

from rosace.models import Observation
from rosace.plugins.reduction import ReductionPlugin


def _make_obs(with_raw_files=True, with_program=True) -> Observation:
    obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")
    if with_raw_files:
        obs.sequencer_data["star_spectra_images"] = ["/tmp/spectrum_0001.fits"]
    if with_program:
        obs.scheduler_data["program"] = "etoiles_be"
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
        obs.reduction_data.setdefault("order", []).append(self.name)
        return obs


class TestReductionPlugin:

    async def test_raises_if_no_raw_files(self):
        obs = _make_obs(with_raw_files=False)
        with pytest.raises(RuntimeError, match="star_spectra_images"):
            await ReductionPlugin().run(obs)

    async def test_raises_if_program_missing(self):
        obs = _make_obs(with_program=False)
        with pytest.raises(RuntimeError, match="program missing"):
            await ReductionPlugin().run(obs)

    async def test_calls_subplugins_in_order(self, monkeypatch):
        calls: list[str] = []
        fake_pipeline = [
            FakeSubPlugin("bias", calls),
            FakeSubPlugin("dark", calls),
            FakeSubPlugin("flat", calls),
            FakeSubPlugin("extract", calls),
        ]
        monkeypatch.setattr(
            "rosace.plugins.reduction.load_pipelines",
            lambda program: ([], fake_pipeline),
        )

        obs = _make_obs()
        result = await ReductionPlugin().run(obs)

        assert calls == ["bias", "dark", "flat", "extract"]
        assert result.reduction_data["order"] == ["bias", "dark", "flat", "extract"]

    async def test_loads_pipeline_for_observation_program(self, monkeypatch):
        seen_program = []

        def fake_load_pipelines(program):
            seen_program.append(program)
            return [], []

        monkeypatch.setattr("rosace.plugins.reduction.load_pipelines", fake_load_pipelines)

        obs = _make_obs()
        obs.scheduler_data["program"] = "nova_monitoring"
        await ReductionPlugin().run(obs)

        assert seen_program == ["nova_monitoring"]

    async def test_subplugin_error_propagates(self, monkeypatch):
        calls: list[str] = []
        fake_pipeline = [
            FakeSubPlugin("bias", calls),
            FakeSubPlugin("dark", calls, raises=RuntimeError("dark failed")),
            FakeSubPlugin("flat", calls),
        ]
        monkeypatch.setattr(
            "rosace.plugins.reduction.load_pipelines",
            lambda program: ([], fake_pipeline),
        )

        obs = _make_obs()
        with pytest.raises(RuntimeError, match="dark failed"):
            await ReductionPlugin().run(obs)

        assert calls == ["bias", "dark"]  # flat never reached
