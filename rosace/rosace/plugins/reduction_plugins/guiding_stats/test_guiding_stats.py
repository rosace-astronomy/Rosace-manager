"""
Tests for the guiding statistics sub-plugin (GuidingStatsPlugin), on small
guide logs in PHD2's format.
Run: pytest plugins/reduction_plugins/guiding_stats/test_guiding_stats.py -v
"""

import logging
from datetime import datetime, timezone

import pytest

from rosace.models import Observation
from rosace.phd2.guide_log import CSV_HEADER
from rosace.plugins.reduction_plugins.guiding_stats import guiding_stats as module
from rosace.plugins.reduction_plugins.guiding_stats.guiding_stats import GuidingStatsPlugin

NAME = "20261016T201852_HD_7636_6a794eb7_PHD2_GuideLog.txt"


def _row(frame, ra, dec):
    return f'{frame},{frame * 3.0:.3f},"Mount",0.0,0.0,{ra},{dec},0.000,0.000,0,,0,,,,1000,30.00,0'


SETTLED = "INFO: SETTLING STATE CHANGE, Settling complete"
# Before settling: the lock position jump, never measured
BEFORE = ["INFO: SETTLING STATE CHANGE, Settling started", _row(1, 9.0, -9.0), SETTLED,
          "INFO: SET LOCK POSITION, new lock pos = 546.997, 675.303", _row(2, 20.0, 20.0),
          "INFO: SETTLING STATE CHANGE, Settling started"]


def _log(rows, before=BEFORE):
    lines = ["Guiding Begins at 2026-10-16 20:18:52", "Pixel scale = 2.88 arc-sec/px", CSV_HEADER, *before, *rows,
             "Guiding Ends at 2026-10-16 20:20:22"]
    return "\n".join(lines) + "\n"


def _setup(tmp_path, text, monkeypatch):
    monkeypatch.setattr(module, "SUBPLUGIN_PARAMS", {"guiding": {"sampling_arcsec": 2.0}})
    (tmp_path / NAME).write_text(text)
    obs = Observation(id="6a794eb7", created_at=datetime.now(timezone.utc), session_id="sess1234", obs_dir=str(tmp_path))
    obs.sequencer_data["guide_log_file"] = NAME
    return obs


class TestGuidingStatsPlugin:

    async def test_statistics_after_the_last_settling(self, tmp_path, caplog, monkeypatch):
        rows = [_row(3, 5.0, 5.0), SETTLED, _row(4, 1.0, -1.0), _row(5, -1.0, 1.0), _row(6, 1.0, -3.0),
                _row(7, -1.0, 3.0), '8,24.000,"DROP",,,,,,,,,,,,,0,0.00,2,"Star lost - low SNR"']
        obs = _setup(tmp_path, _log(rows), monkeypatch)

        with caplog.at_level(logging.INFO, logger="spectro.subplugin.guiding_stats"):
            result = await GuidingStatsPlugin().execute(obs)

        stats = result.reduction_data["guiding_stats"]
        assert stats["ra_rms_px"] == pytest.approx(1.0) and stats["dec_rms_px"] == pytest.approx(5 ** 0.5, abs=1e-3)
        assert stats["ra_rms_arcsec"] == 2.0 and stats["dec_rms_arcsec"] == pytest.approx(4.47)
        assert stats["total_rms_arcsec"] == pytest.approx(2 * 6 ** 0.5, abs=0.01)
        assert (stats["ra_max_arcsec"], stats["dec_max_arcsec"]) == (2.0, 6.0)
        assert (stats["frames"], stats["dropped"], stats["duration_s"], stats["pixel_scale"]) == (5, 1, 15.0, 2.0)
        assert "[GUIDING_STATS] Guiding RMS 4.90\"" in caplog.text

    async def test_never_settled_is_only_a_warning(self, tmp_path, caplog, monkeypatch):
        before = ["INFO: SETTLING STATE CHANGE, Settling started", _row(1, 9.0, -9.0)]
        obs = _setup(tmp_path, _log([_row(2, 1.0, 0.0), "INFO: SETTLING STATE CHANGE, Settling failed"], before),
                     monkeypatch)

        with caplog.at_level(logging.WARNING, logger="spectro.subplugin.guiding_stats"):
            result = await GuidingStatsPlugin().execute(obs)

        assert "guiding_stats" not in result.reduction_data
        assert "never completed its settling" in caplog.text

    async def test_missing_guide_log_is_only_a_warning(self, tmp_path, caplog):
        obs = Observation(id="x", created_at=datetime.now(timezone.utc), session_id="s", obs_dir=str(tmp_path))

        with caplog.at_level(logging.WARNING, logger="spectro.subplugin.guiding_stats"):
            result = await GuidingStatsPlugin().execute(obs)

        assert "guiding_stats" not in result.reduction_data
        assert "[GUIDING_STATS] Not measured" in caplog.text and "guide_log_file" in caplog.text

    async def test_no_guided_frame_is_only_a_warning(self, tmp_path, caplog, monkeypatch):
        obs = _setup(tmp_path, _log([SETTLED, '3,9.000,"DROP",,,,,,,,,,,,,0,0.00,2,"Star lost"']), monkeypatch)

        with caplog.at_level(logging.WARNING, logger="spectro.subplugin.guiding_stats"):
            result = await GuidingStatsPlugin().execute(obs)

        assert "guiding_stats" not in result.reduction_data
        assert "no guided frame after settling" in caplog.text
