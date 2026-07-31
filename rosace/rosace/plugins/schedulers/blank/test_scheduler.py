"""
Tests for BlankPlugin — the minimal SCHEDULER example/template.
Run: pytest plugins/schedulers/blank/test_scheduler.py -v
"""

import uuid
from datetime import datetime, timezone

import pytest_asyncio




from rosace.conftest import assert_scheduler_contract
from rosace.database import db_init, db_insert_session, db_insert_observation
from rosace.models import Observation, SessionContext
from rosace.plugins.schedulers.blank.scheduler import BlankPlugin


class TestBlankPlugin:

    @pytest_asyncio.fixture(autouse=True)
    async def init_db(self):
        await db_init()

    async def _make_session(self) -> SessionContext:
        session = SessionContext(site_name="Test")
        await db_insert_session(session)
        return session

    def _make_obs(self, session_id: str) -> Observation:
        return Observation(
            id=str(uuid.uuid4())[:8],
            created_at=datetime.now(timezone.utc),
            session_id=session_id,
        )

    async def test_hands_out_the_three_targets_in_order_then_none(self):
        session = await self._make_session()

        obs1 = await BlankPlugin().run(self._make_obs(session.session_id))
        assert obs1.scheduler_data["target"].name == "Vega"
        assert obs1.scheduler_data["priority"] == 1
        assert_scheduler_contract(obs1)
        await db_insert_observation(obs1, session.session_id)

        obs2 = await BlankPlugin().run(self._make_obs(session.session_id))
        assert obs2.scheduler_data["target"].name == "Arcturus"
        assert obs2.scheduler_data["priority"] == 2
        assert_scheduler_contract(obs2)
        await db_insert_observation(obs2, session.session_id)

        obs3 = await BlankPlugin().run(self._make_obs(session.session_id))
        assert obs3.scheduler_data["target"].name == "Castor"
        assert obs3.scheduler_data["priority"] == 3
        assert_scheduler_contract(obs3)
        await db_insert_observation(obs3, session.session_id)

        obs4 = await BlankPlugin().run(self._make_obs(session.session_id))
        assert obs4.scheduler_data["target"] is None
