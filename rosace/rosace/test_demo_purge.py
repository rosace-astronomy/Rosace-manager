"""
Tests for demo_purge.py: what a demo purge removes and keeps, and its guard
against a running demo server. The functions are tested on a temporary
directory; purge_demo() itself only adds the configuration's paths.
Run: pytest test_demo_purge.py -v
"""

from __future__ import annotations

import socket

from rosace.demo_purge import demo_entries, demo_server_running, remove, report


def _demo_dir(tmp_path):
    """A demo data directory whose replay entry is a link to a dataset
    outside it, as created by hand for --demo-mode."""
    dataset = tmp_path / "replay_data"
    dataset.mkdir()
    (dataset / "manifest.yaml").write_text("stars: []\n")
    base = tmp_path / "rosace-demo"
    (base / "databases").mkdir(parents=True)
    (base / "databases" / "rosace.db").write_bytes(b"x" * 100)
    (base / "observations" / "night").mkdir(parents=True)
    (base / "observations" / "night" / "frame.fits").write_bytes(b"y" * 50)
    (base / "replay").symlink_to(dataset)
    return base, dataset


class TestDemoEntries:

    def test_everything_but_the_replay_dataset(self, tmp_path):
        base, _ = _demo_dir(tmp_path)
        entries = demo_entries(base, base / "replay")
        assert [e.path.name for e in entries] == ["databases", "observations"]
        assert [(e.files, e.size) for e in entries] == [(1, 100), (1, 50)]

    def test_missing_directory_has_nothing_to_purge(self, tmp_path):
        assert demo_entries(tmp_path / "none", None) == []

    def test_report_totals(self, tmp_path):
        base, _ = _demo_dir(tmp_path)
        assert "Total: 2 entries, 2 files" in report(demo_entries(base, base / "replay"))


class TestRemove:

    def test_removes_the_entries_and_keeps_the_dataset_and_its_link(self, tmp_path):
        base, dataset = _demo_dir(tmp_path)
        remove(demo_entries(base, base / "replay"))
        assert [p.name for p in base.iterdir()] == ["replay"]
        assert (base / "replay").is_symlink()
        assert (dataset / "manifest.yaml").is_file()


class TestServerRunning:

    def test_listening_port_means_running(self):
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            assert demo_server_running("127.0.0.1", server.getsockname()[1])

    def test_closed_port_means_not_running(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        assert not demo_server_running("127.0.0.1", port)
