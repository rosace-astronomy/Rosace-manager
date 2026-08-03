"""
Fixtures pytest partagées par tous les fichiers de tests du paquet
(test_rosace_core.py, plugins/test_*.py, plugins/*_plugins/test_*.py).
"""

import pytest

from rosace.models import Observation, Target, ExposureConfig


@pytest.fixture(scope="session", autouse=True)
def tmp_data_dir(tmp_path_factory):
    """
    Remplace BASE_DATA_DIR par un répertoire temporaire pour toute la session.
    Évite de polluer ~/.rosace ou les bases de données de production.
    """
    tmp = tmp_path_factory.mktemp("rosace_test_data")
    # Patch avant le premier import du package rosace
    import rosace.config as cfg
    cfg.BASE_DATA_DIR      = tmp
    cfg.DATABASES_DIR      = tmp
    cfg.DB_PATH            = tmp / "rosace.db"
    cfg.LOG_DIR            = tmp
    cfg.OBSERVATIONS_DIR   = tmp / "observations"
    import rosace.targets as tgt
    tgt.DATABASES_DIR   = tmp
    tgt.TARGETS_DB_PATH = tmp / "targets.db"
    import rosace.database as db_mod
    db_mod.DATABASES_DIR = tmp
    db_mod.DB_PATH        = tmp / "rosace.db"
    import rosace.auth as auth_mod
    auth_mod.DB_PATH = tmp / "rosace.db"
    import rosace.log_config as log_cfg_mod
    log_cfg_mod.LOG_DIR = tmp
    import rosace.plugins.init_session as init_session_mod
    init_session_mod.OBSERVATIONS_DIR = tmp / "observations"
    return tmp


def assert_scheduler_contract(obs: Observation) -> None:
    """
    Canonical SCHEDULER contract check, shared by every SCHEDULER plugin's own
    test suite (plugins/schedulers/<name>/test_scheduler.py) — see the full
    contract description in plugins/schedulers/blank/scheduler.py. Deliberately
    not enforced at runtime in plugin code itself (see plugins/schedulers/), only
    checked here in tests, with explicit failure messages naming the offending
    key and its actual value.

    Call this after a run() that selected a target (obs.scheduler_data["target"]
    is not None) — an empty night (target=None) is a valid outcome with nothing
    else to check.
    """
    data = obs.scheduler_data
    assert isinstance(data.get("target"), Target), (
        f"scheduler_data['target'] must be a Target instance, got {data.get('target')!r}"
    )
    assert isinstance(data.get("queried_name"), str) and data["queried_name"], (
        f"scheduler_data['queried_name'] must be a non-empty string, got {data.get('queried_name')!r}"
    )
    assert isinstance(data.get("programme"), str) and data["programme"], (
        f"scheduler_data['programme'] must be a non-empty string, got {data.get('programme')!r}"
    )
    assert isinstance(data.get("priority"), int), (
        f"scheduler_data['priority'] must be an int, got {data.get('priority')!r}"
    )
    assert isinstance(data.get("exposure"), ExposureConfig), (
        f"scheduler_data['exposure'] must be an ExposureConfig instance, got {data.get('exposure')!r}"
    )
