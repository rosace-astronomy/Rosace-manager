"""Shared fixture of the replay tests: the synthetic dataset of testing.py,
installed as the shared bench."""

from __future__ import annotations

import pytest

from rosace.hardware_replay.bench import ReplayBench, set_bench
from rosace.hardware_replay.testing import make_dataset


@pytest.fixture
def replay_bench(tmp_path):
    bench = ReplayBench(make_dataset(tmp_path / "replay"))
    set_bench(bench)
    yield bench
    set_bench(None)
