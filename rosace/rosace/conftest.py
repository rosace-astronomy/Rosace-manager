"""
Pytest fixtures shared by every test file in the package
(test_rosace_core.py, plugins/test_*.py, plugins/*_plugins/test_*.py).
"""

import pytest

from rosace import step_progress
from rosace.models import Observation, Target, ExposureConfig


@pytest.fixture(autouse=True)
def _clear_step_progress():
    """step_progress._current_steps is process-global, in-memory state (by
    design -- see step_progress.py's own docstring) -- without this, a test
    exercising the real SequencerPlugin/ReductionPlugin (which call the real
    step_progress.set_step, not a mock) leaks an entry that a later,
    unrelated test on step_progress itself would see."""
    yield
    step_progress._current_steps.clear()


@pytest.fixture(scope="session", autouse=True)
def tmp_data_dir(tmp_path_factory):
    """
    Replaces BASE_DATA_DIR with a temporary directory for the whole session.
    Avoids polluting ~/.rosace or production databases.
    """
    tmp = tmp_path_factory.mktemp("rosace_test_data")
    # Patched before the rosace package's first import
    import rosace.config as cfg
    cfg.BASE_DATA_DIR      = tmp
    cfg.DATABASES_DIR      = tmp
    cfg.DB_PATH            = tmp / "rosace.db"
    cfg.LOG_DIR            = tmp
    cfg.OBSERVATIONS_DIR   = tmp / "observations"
    cfg.GUI_PREVIEW_DIR    = tmp / "gui_preview"
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
    import rosace.session_log_extractor as session_log_mod
    session_log_mod.LOG_DIR = tmp
    import rosace.plugins.init_session as init_session_mod
    init_session_mod.OBSERVATIONS_DIR = tmp / "observations"
    import rosace.plugins.sequencer_plugins.acquisition_helpers as acq_helpers_mod
    acq_helpers_mod.GUI_PREVIEW_DIR = tmp / "gui_preview"
    # rosace.main itself is deliberately NOT imported/patched here: importing it runs
    # configure_logging() (module level), which applies conf/logging.cfg's propagate=0
    # on the "spectro" logger -- fine normally, but forcing that as a side effect of
    # this autouse session fixture broke caplog-based tests in files that otherwise
    # never import rosace.main (found the hard way: 5 failures across two unrelated
    # test files). Tests that exercise main.py's own GET /images/{panel} route patch
    # main's GUI_PREVIEW_DIR locally instead (see test_rosace_core.py).
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
    else to check. The optional keys (proposed_by, estimated_duration_s) are
    checked only when present.
    """
    data = obs.scheduler_data
    assert isinstance(data.get("target"), Target), (
        f"scheduler_data['target'] must be a Target instance, got {data.get('target')!r}"
    )
    assert isinstance(data.get("queried_name"), str) and data["queried_name"], (
        f"scheduler_data['queried_name'] must be a non-empty string, got {data.get('queried_name')!r}"
    )
    assert isinstance(data.get("program"), str) and data["program"], (
        f"scheduler_data['program'] must be a non-empty string, got {data.get('program')!r}"
    )
    assert isinstance(data.get("priority"), int), (
        f"scheduler_data['priority'] must be an int, got {data.get('priority')!r}"
    )
    assert isinstance(data.get("exposure"), ExposureConfig), (
        f"scheduler_data['exposure'] must be an ExposureConfig instance, got {data.get('exposure')!r}"
    )
    if "proposed_by" in data:
        assert isinstance(data["proposed_by"], str) and data["proposed_by"], (
            f"scheduler_data['proposed_by'] must be a non-empty string, got {data['proposed_by']!r}"
        )
    if "estimated_duration_s" in data:
        duration = data["estimated_duration_s"]
        assert isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration > 0, (
            f"scheduler_data['estimated_duration_s'] must be a positive number, got {duration!r}"
        )
