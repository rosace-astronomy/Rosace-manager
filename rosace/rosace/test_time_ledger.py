"""Tests for time_ledger.py: the session's time line, recorded by the orchestrator."""

import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from rosace import database as db_mod
from rosace import time_ledger as tl_mod
from rosace.database import db_init
from rosace.time_ledger import TimeLedger


def _rows(session_id: str) -> list[tuple]:
    with sqlite3.connect(db_mod.DB_PATH) as db:
        return db.execute(
            "SELECT category, step, observation_id, outcome, started_at, ended_at "
            "FROM time_segments WHERE session_id = ? ORDER BY started_at, id",
            (session_id,),
        ).fetchall()


@pytest.fixture
async def ledger():
    await db_init()
    return TimeLedger()


async def test_switch_closes_the_current_interval_and_opens_the_next(ledger):
    sid = uuid.uuid4().hex[:8]
    ledger.start(sid, "init", at=datetime.now(timezone.utc) - timedelta(seconds=5))
    await ledger.switch("scheduler")
    await ledger.switch("pointing", "slew_72ed", "obs1")
    await ledger.stop()

    rows = _rows(sid)
    assert [r[:3] for r in rows] == [("init", None, None), ("scheduler", None, None),
                                     ("pointing", "slew_72ed", "obs1")]
    # Consecutive intervals: each one starts where the previous one ended.
    for previous, current in zip(rows, rows[1:]):
        assert current[4] == previous[5]


async def test_mark_failed_marks_recorded_and_current_intervals(ledger):
    sid = uuid.uuid4().hex[:8]
    ledger.start(sid, "init", at=datetime.now(timezone.utc) - timedelta(seconds=5))
    await ledger.switch("pointing", "slew_72ed", "obsF")
    await ledger.switch("pointing", "pointing_72ed", "obsF")
    await ledger.mark_failed("obsF")
    await ledger.switch("orchestrator")
    await ledger.stop()

    rows = [r for r in _rows(sid) if r[2] == "obsF"]
    assert rows and all(r[3] == "failed" for r in rows)


async def test_without_a_session_nothing_is_recorded(ledger):
    await ledger.switch("scheduler")
    await ledger.mark_failed("x")
    await ledger.stop()
    assert ledger.session_id is None


async def test_a_database_error_never_blocks(ledger, monkeypatch, caplog):
    async def broken(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(tl_mod, "db_insert_time_segment", broken)
    ledger.start("s-broken", "init", at=datetime.now(timezone.utc) - timedelta(seconds=5))
    with caplog.at_level(logging.WARNING):
        await ledger.switch("scheduler")
    assert any("Could not record the 'init' interval" in r.getMessage() for r in caplog.records)
