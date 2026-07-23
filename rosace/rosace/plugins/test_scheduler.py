"""
Tests du plugin SCHEDULER (SchedulerPlugin).
Lancement : pytest plugins/test_scheduler.py -v
"""

from datetime import datetime, timezone

import pytest

import rosace.main as main_mod
from rosace.models import Observation
from rosace.plugins.scheduler import SchedulerPlugin, SchedulerStopRequested


def _make_obs() -> Observation:
    return Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")


@pytest.fixture(autouse=True)
def reset_state():
    """Isole chaque test : état propre avant et après."""
    main_mod.state.is_running = False
    yield
    main_mod.state.is_running = False


class TestSchedulerPlugin:

    async def test_fills_scheduler_data_when_running(self):
        main_mod.state.is_running = True
        obs = _make_obs()

        result = await SchedulerPlugin().run(obs)

        assert result is obs
        assert obs.scheduler_data["target"].name == "Vega"
        assert obs.scheduler_data["exposure"].exposure_time == 300.0
        assert obs.scheduler_data["programme"] == "etoiles_be"

    async def test_raises_stop_requested_when_not_running(self):
        main_mod.state.is_running = False
        obs = _make_obs()

        with pytest.raises(SchedulerStopRequested):
            await SchedulerPlugin().run(obs)

        assert obs.scheduler_data == {}  # rien posé avant l'arrêt
