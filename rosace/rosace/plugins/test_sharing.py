"""
Tests du plugin SHARING (SharingPlugin).
Le chargement du sous-plugin (pipeline_loader.load_sharing_plugin) est
mocké : ces tests couvrent l'orchestration, pas le comportement du vrai
sous-plugin (couvert par sharing_plugins/<name>/test_<name>.py).
Lancement : pytest plugins/test_sharing.py -v
"""

from datetime import datetime, timezone

import pytest

from rosace.models import Observation
from rosace.plugins.sharing import SharingPlugin


def _make_obs(with_programme=True) -> Observation:
    obs = Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")
    if with_programme:
        obs.scheduler_data["programme"] = "etoiles_be"
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

    async def test_raises_if_programme_missing(self):
        obs = _make_obs(with_programme=False)
        with pytest.raises(RuntimeError, match="programme manquant"):
            await SharingPlugin().run(obs)

    async def test_calls_sharing_subplugin(self, monkeypatch):
        calls: list[str] = []
        fake_sub = FakeSharingSubPlugin(calls)
        monkeypatch.setattr("rosace.plugins.sharing.load_sharing_plugin", lambda programme: fake_sub)

        obs = _make_obs()
        result = await SharingPlugin().run(obs)

        assert calls == ["shared"]
        assert result.sharing_data["fake"] == {"done": True}

    async def test_loads_sharing_plugin_for_observation_programme(self, monkeypatch):
        seen_programme = []

        def fake_load_sharing_plugin(programme):
            seen_programme.append(programme)
            return FakeSharingSubPlugin([])

        monkeypatch.setattr("rosace.plugins.sharing.load_sharing_plugin", fake_load_sharing_plugin)

        obs = _make_obs()
        obs.scheduler_data["programme"] = "nova_monitoring"
        await SharingPlugin().run(obs)

        assert seen_programme == ["nova_monitoring"]

    async def test_subplugin_error_propagates(self, monkeypatch):
        fake_sub = FakeSharingSubPlugin([], raises=RuntimeError("sharing failed"))
        monkeypatch.setattr("rosace.plugins.sharing.load_sharing_plugin", lambda programme: fake_sub)

        obs = _make_obs()
        with pytest.raises(RuntimeError, match="sharing failed"):
            await SharingPlugin().run(obs)
