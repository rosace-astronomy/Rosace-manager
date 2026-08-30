"""
Tests for session_log_extractor.py.
Run: pytest test_session_log_extractor.py -v
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import rosace.session_log_extractor as session_log_extractor_mod
from rosace.models import SessionContext
from rosace.session_log_extractor import extract_session_log
from rosace.time_helpers import astronomical_day_local_date

STARTED_AT = datetime(2026, 8, 29, 21, 34, 50, tzinfo=timezone.utc)


def _make_ctx(session_dir: Path, started_at: datetime = STARTED_AT) -> SessionContext:
    return SessionContext(
        session_id="086d509a", started_at=started_at, site_name="St-Pancrasse",
        session_dir=str(session_dir),
    )


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


class TestExtractSessionLog:

    def test_no_log_files_writes_header_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr(session_log_extractor_mod, "LOG_DIR", tmp_path / "log")
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        ctx = _make_ctx(session_dir)

        output_path = extract_session_log(ctx)

        assert output_path == session_dir / "20260829T213450_086d509a_session.log"
        content = output_path.read_text()
        assert "ROSACE session log" in content
        assert content.rstrip().endswith("═" * 78) or "Extracted from:" in content
        assert "WARNING: no log lines found at all" in content

    def test_ends_with_end_of_file_footer(self, tmp_path, monkeypatch):
        log_dir = tmp_path / "log"
        log_dir.mkdir()
        monkeypatch.setattr(session_log_extractor_mod, "LOG_DIR", log_dir)
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        ctx = _make_ctx(session_dir)
        (log_dir / "rosace.log").write_text(f"{_fmt(STARTED_AT + timedelta(seconds=1))} - INFO - some line\n")

        output_path = extract_session_log(ctx)

        content = output_path.read_text()
        assert content.rstrip().endswith("#" + "═" * 78)
        assert "# End of file" in content
        # The footer's rule must come after the last real log line, not before it.
        assert content.index("some line") < content.rindex("# End of file")

    def test_no_warning_when_earliest_line_predates_session_start(self, tmp_path, monkeypatch):
        """Normal case: the oldest surviving file still has content from
        before the session started, proving nothing was rotated away."""
        log_dir = tmp_path / "log"
        log_dir.mkdir()
        monkeypatch.setattr(session_log_extractor_mod, "LOG_DIR", log_dir)
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        ctx = _make_ctx(session_dir)

        before = _fmt(STARTED_AT - timedelta(minutes=5))
        (log_dir / "rosace.log").write_text(f"{before} - INFO - idle, before this session\n")

        output_path = extract_session_log(ctx)

        assert "WARNING" not in output_path.read_text()

    def test_no_warning_for_small_second_precision_jitter(self, tmp_path, monkeypatch):
        """The very first log line of a session can land 0-1s after
        started_at (second-precision timestamps, tiny scheduling delay) --
        must not be misreported as rotated-away history."""
        log_dir = tmp_path / "log"
        log_dir.mkdir()
        monkeypatch.setattr(session_log_extractor_mod, "LOG_DIR", log_dir)
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        ctx = _make_ctx(session_dir)

        just_after = _fmt(STARTED_AT + timedelta(seconds=1))
        (log_dir / "rosace.log").write_text(f"{just_after} - INFO - [INIT_SESSION] Starting session\n")

        output_path = extract_session_log(ctx)

        assert "WARNING" not in output_path.read_text()

    def test_warns_when_early_session_history_was_rotated_out(self, tmp_path, monkeypatch):
        log_dir = tmp_path / "log"
        log_dir.mkdir()
        monkeypatch.setattr(session_log_extractor_mod, "LOG_DIR", log_dir)
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        ctx = _make_ctx(session_dir)

        # The oldest surviving line is well after started_at -- everything
        # earlier has already been rotated out.
        gap_start = STARTED_AT + timedelta(minutes=12)
        (log_dir / "rosace.log").write_text(f"{_fmt(gap_start)} - INFO - first surviving line\n")

        output_path = extract_session_log(ctx)

        content = output_path.read_text()
        assert "WARNING: earlier session history is missing" in content
        assert "first surviving line" in content  # still extracted, just flagged as incomplete

    def test_filters_lines_before_and_after_started_at(self, tmp_path, monkeypatch):
        log_dir = tmp_path / "log"
        log_dir.mkdir()
        monkeypatch.setattr(session_log_extractor_mod, "LOG_DIR", log_dir)
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        ctx = _make_ctx(session_dir)

        before = _fmt(STARTED_AT - timedelta(minutes=5))
        after = _fmt(STARTED_AT + timedelta(minutes=5))
        (log_dir / "rosace.log").write_text(
            f"{before} - INFO - before the session, must be dropped\n"
            f"{after} - INFO - during the session, must be kept\n"
        )

        output_path = extract_session_log(ctx)

        content = output_path.read_text()
        assert "before the session" not in content
        assert "during the session, must be kept" in content

    def test_reads_rotated_backups_oldest_first(self, tmp_path, monkeypatch):
        log_dir = tmp_path / "log"
        log_dir.mkdir()
        monkeypatch.setattr(session_log_extractor_mod, "LOG_DIR", log_dir)
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        ctx = _make_ctx(session_dir)

        t0 = _fmt(STARTED_AT + timedelta(minutes=1))
        t1 = _fmt(STARTED_AT + timedelta(minutes=2))
        t2 = _fmt(STARTED_AT + timedelta(minutes=3))
        (log_dir / "rosace.log.2").write_text(f"{t0} - INFO - oldest chunk\n")
        (log_dir / "rosace.log.1").write_text(f"{t1} - INFO - middle chunk\n")
        (log_dir / "rosace.log").write_text(f"{t2} - INFO - newest chunk\n")

        output_path = extract_session_log(ctx)

        content = output_path.read_text()
        assert content.index("oldest chunk") < content.index("middle chunk") < content.index("newest chunk")

    def test_continuation_line_follows_the_previous_record_kept_status(self, tmp_path, monkeypatch):
        log_dir = tmp_path / "log"
        log_dir.mkdir()
        monkeypatch.setattr(session_log_extractor_mod, "LOG_DIR", log_dir)
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        ctx = _make_ctx(session_dir)

        before = _fmt(STARTED_AT - timedelta(minutes=5))
        after = _fmt(STARTED_AT + timedelta(minutes=5))
        (log_dir / "rosace.log").write_text(
            f"{before} - ERROR - dropped record\n"
            "  continuation of the dropped record\n"
            f"{after} - ERROR - kept record\n"
            "  continuation of the kept record\n"
        )

        output_path = extract_session_log(ctx)

        content = output_path.read_text()
        assert "continuation of the dropped record" not in content
        assert "continuation of the kept record" in content

    def test_header_contains_session_identity(self, tmp_path, monkeypatch):
        monkeypatch.setattr(session_log_extractor_mod, "LOG_DIR", tmp_path / "log")
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        ctx = _make_ctx(session_dir)

        output_path = extract_session_log(ctx)

        content = output_path.read_text()
        assert ctx.session_id in content
        assert ctx.site_name in content
        assert astronomical_day_local_date(ctx.started_at) in content
        assert "2026-08-29T21:34:50Z" in content

    def test_two_sessions_the_same_night_produce_distinct_files(self, tmp_path, monkeypatch):
        """Several sessions can start within the same astronomical night
        (weather permitting a restart, several short debug runs) and share
        the same session_dir -- a fixed session.log name would let a later
        one silently overwrite an earlier one's extract."""
        monkeypatch.setattr(session_log_extractor_mod, "LOG_DIR", tmp_path / "log")
        session_dir = tmp_path / "session"
        session_dir.mkdir()
        ctx_a = SessionContext(
            session_id="aaaaaaaa", started_at=STARTED_AT, site_name="St-Pancrasse",
            session_dir=str(session_dir),
        )
        ctx_b = SessionContext(
            session_id="bbbbbbbb", started_at=STARTED_AT + timedelta(hours=1), site_name="St-Pancrasse",
            session_dir=str(session_dir),
        )

        path_a = extract_session_log(ctx_a)
        path_b = extract_session_log(ctx_b)

        assert path_a != path_b
        assert path_a.exists() and path_b.exists()
