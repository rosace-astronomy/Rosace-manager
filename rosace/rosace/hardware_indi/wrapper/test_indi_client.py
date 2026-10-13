"""
Tests for indi_client.py's reset_shared_clients() (no INDI server needed:
fake clients are placed in the singleton cache).
Run: pytest hardware_indi/wrapper/test_indi_client.py -v
"""

from unittest.mock import MagicMock

import pytest

from rosace.hardware_indi.wrapper.indi_client import SingletonIndiClientHolder, reset_shared_clients


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch):
    monkeypatch.setattr(SingletonIndiClientHolder, "_instances", {})


def test_closes_every_client_and_empties_the_cache():
    a, b = MagicMock(), MagicMock()
    a.isServerConnected.return_value = False      # its state is not trusted: closed anyway
    SingletonIndiClientHolder._instances.update({"host-a:7624": a, "host-b:7624": b})

    reset_shared_clients()

    a.disconnectServer.assert_called_once_with()
    b.disconnectServer.assert_called_once_with()
    assert SingletonIndiClientHolder._instances == {}


def test_a_failing_close_does_not_stop_the_others():
    a, b = MagicMock(), MagicMock()
    a.disconnectServer.side_effect = RuntimeError("socket already gone")
    SingletonIndiClientHolder._instances.update({"host-a:7624": a, "host-b:7624": b})

    reset_shared_clients()

    b.disconnectServer.assert_called_once_with()
    assert SingletonIndiClientHolder._instances == {}


def test_empty_cache_is_a_no_op():
    reset_shared_clients()
    assert SingletonIndiClientHolder._instances == {}
