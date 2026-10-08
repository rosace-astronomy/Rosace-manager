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
        """Arbitrary generic labels, not real sub-plugin names -- SequencerPlugin
        must stay agnostic of which sub-plugins actually exist (see CLAUDE.md).
        obs.program_steps["sequencer"] is zipped against the pipeline instances
        (see sequencer.py), so the real program YAML's step count must also be
        mocked here -- otherwise this test would silently depend on however
        many steps demo.yaml happens to declare today."""
        calls: list[str] = []
        fake_pipeline = [
            FakeSubPlugin("step_a", calls),
            FakeSubPlugin("step_b", calls),
            FakeSubPlugin("step_c", calls),
        ]
        monkeypatch.setattr(
            "rosace.plugins.sequencer.load_pipelines",
            lambda program: (fake_pipeline, []),
        )
        monkeypatch.setattr(
            "rosace.plugins.sequencer.load_program_yaml",
            lambda program: {
                "sequencer": ["step_a", "step_b", "step_c"], "reduction": [], "sharing": "blank_share",
            },
        )

        obs = _make_obs()
        result = await SequencerPlugin().run(obs)

        assert calls == ["step_a", "step_b", "step_c"]
        assert result.sequencer_data["order"] == ["step_a", "step_b", "step_c"]

    async def test_sets_program_file_and_steps_including_sharing(self, monkeypatch):
        """obs.program_steps must list sequencer, reduction AND sharing —
        otherwise a reader of the observation's YAML can't know which SHARING
        sub-plugin was planned if the observation crashes before it runs."""
        monkeypatch.setattr(
            "rosace.plugins.sequencer.hardware_sequencer_prefix",
            lambda: [],
        )
        monkeypatch.setattr(
            "rosace.plugins.sequencer.load_pipelines",
            lambda program: ([], []),
        )

        obs = _make_obs()  # program = "demo"
        result = await SequencerPlugin().run(obs)

        assert result.program_file == "demo.yaml"
        assert result.program_steps == {
            "sequencer": ["slew", "star_spectra_acquisition"],
            "reduction": [],
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
        """Arbitrary generic labels, not real sub-plugin names -- see
        test_calls_subplugins_in_order above."""
        calls: list[str] = []
        fake_pipeline = [
            FakeSubPlugin("step_a", calls),
            FakeSubPlugin("step_b", calls, raises=RuntimeError("step_b failed")),
            FakeSubPlugin("step_c", calls),
        ]
        monkeypatch.setattr(
            "rosace.plugins.sequencer.load_pipelines",
            lambda program: (fake_pipeline, []),
        )
        monkeypatch.setattr(
            "rosace.plugins.sequencer.load_program_yaml",
            lambda program: {
                "sequencer": ["step_a", "step_b", "step_c"], "reduction": [], "sharing": "blank_share",
            },
        )

        obs = _make_obs()
        with pytest.raises(RuntimeError, match="step_b failed"):
            await SequencerPlugin().run(obs)

        assert calls == ["step_a", "step_b"]  # step_c never reached

    async def test_program_steps_include_hardware_sequencer_prefix(self, monkeypatch):
        """program_steps["sequencer"] must include the active hardware
        profile's own prefix (e.g. an AM3 mount's home/sync step) -- both it
        and load_pipelines() read the prefix from the same
        hardware_sequencer_prefix() function, so the two can never drift
        apart (see pipeline_loader.py)."""
        monkeypatch.setattr(
            "rosace.plugins.sequencer.hardware_sequencer_prefix",
            lambda: ["home_sync_am3"],
        )
        monkeypatch.setattr(
            "rosace.plugins.sequencer.load_pipelines",
            lambda program: ([], []),
        )

        obs = _make_obs()  # program = "demo"
        result = await SequencerPlugin().run(obs)

        assert result.program_steps["sequencer"] == [
            "home_sync_am3", "slew", "star_spectra_acquisition",
        ]

    async def test_hardware_prefix_step_names_align_with_executed_pipeline(self, monkeypatch):
        """The prefixed step names in program_steps["sequencer"] must line up
        positionally with the sub-plugin instances load_pipelines() actually
        returns -- run() zips the two together, so a mismatch would silently
        associate the wrong name with the wrong instance."""
        calls: list[str] = []
        fake_pipeline = [FakeSubPlugin("home_sync_am3", calls), FakeSubPlugin("step_a", calls)]
        monkeypatch.setattr(
            "rosace.plugins.sequencer.hardware_sequencer_prefix",
            lambda: ["home_sync_am3"],
        )
        monkeypatch.setattr(
            "rosace.plugins.sequencer.load_program_yaml",
            lambda program: {"sequencer": ["step_a"], "reduction": [], "sharing": "blank_share"},
        )
        monkeypatch.setattr(
            "rosace.plugins.sequencer.load_pipelines",
            lambda program: (fake_pipeline, []),
        )

        obs = _make_obs()
        result = await SequencerPlugin().run(obs)

        assert result.program_steps["sequencer"] == ["home_sync_am3", "step_a"]
        assert calls == ["home_sync_am3", "step_a"]

    async def test_records_step_progress_before_each_subplugin(self, monkeypatch):
        """SequencerPlugin must stay agnostic of which sub-plugins actually
        exist -- both the declared step names and the pipeline instances are
        mocked here with arbitrary labels, not real sub-plugin names, so
        this test only exercises the orchestration mechanism itself."""
        seen = []
        monkeypatch.setattr(
            "rosace.plugins.sequencer.step_progress.set_step",
            lambda obs_id, stage, step, steps: seen.append((obs_id, stage, step, steps)),
        )
        monkeypatch.setattr(
            "rosace.plugins.sequencer.hardware_sequencer_prefix",
            lambda: [],
        )
        monkeypatch.setattr(
            "rosace.plugins.sequencer.load_program_yaml",
            lambda program: {"sequencer": ["step_a", "step_b"], "reduction": [], "sharing": "step_c"},
        )
        fake_pipeline = [FakeSubPlugin("step_a", []), FakeSubPlugin("step_b", [])]
        monkeypatch.setattr(
            "rosace.plugins.sequencer.load_pipelines",
            lambda program: (fake_pipeline, []),
        )

        obs = _make_obs()
        await SequencerPlugin().run(obs)

        steps = ["step_a", "step_b"]
        assert seen == [
            ("obs12345", "sequencer", "step_a", steps),
            ("obs12345", "sequencer", "step_b", steps),
        ]
