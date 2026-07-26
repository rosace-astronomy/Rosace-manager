"""
Tests du plugin SCHEDULER (SchedulerPlugin).
Lancement : pytest plugins/test_scheduler.py -v
"""

from datetime import datetime, timezone

from rosace.models import Observation
from rosace.plugins.scheduler import SchedulerPlugin


def _make_obs() -> Observation:
    return Observation(id="obs12345", created_at=datetime.now(timezone.utc), session_id="sess1234")


class TestSchedulerPlugin:

    async def test_fills_scheduler_data(self):
        obs = _make_obs()

        result = await SchedulerPlugin().run(obs)

        assert result is obs
        assert obs.scheduler_data["target"].name == "Vega"
        assert obs.scheduler_data["exposure"].exposure_time == 300.0
        assert obs.scheduler_data["programme"] == "demo"
