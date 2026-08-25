"""
Tests for the SHARING plugin (SharingPlugin).
Sub-plugin loading (pipeline_loader.load_sharing_plugin) is mocked: these
tests cover orchestration, not the real sub-plugin's behavior (covered by
sharing_plugins/<name>/test_<name>.py).
Run: pytest plugins/test_sharing.py -v
"""

from datetime import datetime, timezone

import pytest

from rosace.models import Observation
from rosace.plugins.sharing import SharingPlugin


def _make_obs(with_program=True) -> Observation:
    obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")
    if with_program:
        obs.scheduler_data["program"] = "etoiles_be"
    return obs


class FakeSharingSubPlugin:
    def __init__(self, calls, raises=None):
        self.calls = calls
        self.raises = raises

    async def execute(self, obs: Observation) -> Observation:
        self.calls.append("shared")
        if self.raises:
            raise self.raises
        obs.sharing_data["fake"] = {"done": True}
        return obs


class TestSharingPlugin:

    async def test_raises_if_program_missing(self):
        obs = _make_obs(with_program=False)
        with pytest.raises(RuntimeError, match="program missing"):
            await SharingPlugin().run(obs)

    async def test_calls_sharing_subplugin(self, monkeypatch):
        calls: list[str] = []
        fake_sub = FakeSharingSubPlugin(calls)
        monkeypatch.setattr("rosace.plugins.sharing.load_sharing_plugin", lambda program: fake_sub)

        obs = _make_obs()
        result = await SharingPlugin().run(obs)

        assert calls == ["shared"]
        assert result.sharing_data["fake"] == {"done": True}

    async def test_loads_sharing_plugin_for_observation_program(self, monkeypatch):
        seen_program = []

        def fake_load_sharing_plugin(program):
            seen_program.append(program)
            return FakeSharingSubPlugin([])

        monkeypatch.setattr("rosace.plugins.sharing.load_sharing_plugin", fake_load_sharing_plugin)

        obs = _make_obs()
        obs.scheduler_data["program"] = "nova_monitoring"
        await SharingPlugin().run(obs)

        assert seen_program == ["nova_monitoring"]

    async def test_subplugin_error_propagates(self, monkeypatch):
        fake_sub = FakeSharingSubPlugin([], raises=RuntimeError("sharing failed"))
        monkeypatch.setattr("rosace.plugins.sharing.load_sharing_plugin", lambda program: fake_sub)

        obs = _make_obs()
        with pytest.raises(RuntimeError, match="sharing failed"):
            await SharingPlugin().run(obs)
