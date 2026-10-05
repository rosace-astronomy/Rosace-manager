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


def _make_obs(with_raw_files=True, with_program=True, reduction_steps=()) -> Observation:
    obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")
    if with_raw_files:
        obs.sequencer_data["star_spectra_images"] = ["/tmp/spectrum_0001.fits"]
    if with_program:
        obs.scheduler_data["program"] = "demo"
    # SequencerPlugin always sets this before REDUCTION runs (see main.py's stage order) --
    # reproduced here since these tests call ReductionPlugin directly, without SEQUENCER.
    obs.program_steps["reduction"] = list(reduction_steps)
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

    async def test_runs_subplugins_without_raw_files(self, monkeypatch):
        """REDUCTION does not gatekeep on science images: each sub-plugin
        validates its own inputs (see CLAUDE.md)."""
        calls: list[str] = []
        monkeypatch.setattr(
            "rosace.plugins.reduction.load_pipelines",
            lambda program: ([], [FakeSubPlugin("step_a", calls)]),
        )

        obs = _make_obs(with_raw_files=False, reduction_steps=["step_a"])
        await ReductionPlugin().run(obs)

        assert calls == ["step_a"]

    async def test_raises_if_program_missing(self):
        obs = _make_obs(with_program=False)
        with pytest.raises(RuntimeError, match="program missing"):
            await ReductionPlugin().run(obs)

    async def test_calls_subplugins_in_order(self, monkeypatch):
        """Arbitrary generic labels, not real sub-plugin names -- ReductionPlugin
        must stay agnostic of which sub-plugins actually exist (see CLAUDE.md)."""
        calls: list[str] = []
        fake_pipeline = [
            FakeSubPlugin("step_a", calls),
            FakeSubPlugin("step_b", calls),
            FakeSubPlugin("step_c", calls),
            FakeSubPlugin("step_d", calls),
        ]
        monkeypatch.setattr(
            "rosace.plugins.reduction.load_pipelines",
            lambda program: ([], fake_pipeline),
        )

        obs = _make_obs(reduction_steps=["step_a", "step_b", "step_c", "step_d"])
        result = await ReductionPlugin().run(obs)

        assert calls == ["step_a", "step_b", "step_c", "step_d"]
        assert result.reduction_data["order"] == ["step_a", "step_b", "step_c", "step_d"]

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
        """Arbitrary generic labels, not real sub-plugin names -- see
        test_calls_subplugins_in_order above."""
        calls: list[str] = []
        fake_pipeline = [
            FakeSubPlugin("step_a", calls),
            FakeSubPlugin("step_b", calls, raises=RuntimeError("step_b failed")),
            FakeSubPlugin("step_c", calls),
        ]
        monkeypatch.setattr(
            "rosace.plugins.reduction.load_pipelines",
            lambda program: ([], fake_pipeline),
        )

        obs = _make_obs(reduction_steps=["step_a", "step_b", "step_c"])
        with pytest.raises(RuntimeError, match="step_b failed"):
            await ReductionPlugin().run(obs)

        assert calls == ["step_a", "step_b"]  # step_c never reached

    async def test_records_step_progress_before_each_subplugin(self, monkeypatch):
        """Arbitrary generic labels, not real sub-plugin names -- see
        test_calls_subplugins_in_order above."""
        seen = []
        monkeypatch.setattr(
            "rosace.plugins.reduction.step_progress.set_step",
            lambda obs_id, stage, step, steps: seen.append((obs_id, stage, step, steps)),
        )
        fake_pipeline = [FakeSubPlugin("step_a", []), FakeSubPlugin("step_b", [])]
        monkeypatch.setattr(
            "rosace.plugins.reduction.load_pipelines",
            lambda program: ([], fake_pipeline),
        )

        obs = _make_obs(reduction_steps=["step_a", "step_b"])
        await ReductionPlugin().run(obs)

        assert seen == [
            ("obs12345", "reduction", "step_a", ["step_a", "step_b"]),
            ("obs12345", "reduction", "step_b", ["step_a", "step_b"]),
        ]
