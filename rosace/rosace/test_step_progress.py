"""
Tests for step_progress.py — in-memory SEQUENCER/REDUCTION step tracking.
Run: pytest test_step_progress.py -v
"""

from rosace import step_progress


class TestSetStep:

    def test_records_step_and_full_list_under_its_own_stage(self):
        step_progress.set_step("obs1", "sequencer", "slew", ["slew", "focus"])

        assert step_progress.get_all() == {
            "obs1": {"sequencer": {"step": "slew", "steps": ["slew", "focus"]}}
        }

    def test_overwrites_previous_entry_for_the_same_stage(self):
        step_progress.set_step("obs1", "sequencer", "slew", ["slew", "focus"])
        step_progress.set_step("obs1", "sequencer", "focus", ["slew", "focus"])

        assert step_progress.get_all()["obs1"]["sequencer"]["step"] == "focus"

    def test_keeps_sequencer_entry_once_reduction_starts(self):
        """The whole point of keying by (obs_id, stage) rather than one
        'current stage' per obs_id -- see step_progress.py's own docstring:
        REDUCTION starting must not erase SEQUENCER's last-known state."""
        step_progress.set_step("obs1", "sequencer", "focus", ["slew", "focus"])
        step_progress.set_step("obs1", "reduction", "bias", ["bias", "dark"])

        entry = step_progress.get_all()["obs1"]
        assert entry["sequencer"] == {"step": "focus", "steps": ["slew", "focus"]}
        assert entry["reduction"] == {"step": "bias", "steps": ["bias", "dark"]}

    def test_tracks_several_observations_independently(self):
        step_progress.set_step("obs1", "sequencer", "slew", ["slew"])
        step_progress.set_step("obs2", "reduction", "bias", ["bias", "dark"])

        all_steps = step_progress.get_all()
        assert set(all_steps["obs1"]) == {"sequencer"}
        assert set(all_steps["obs2"]) == {"reduction"}


class TestGetAll:

    def test_returns_empty_dict_when_nothing_tracked(self):
        assert step_progress.get_all() == {}

    def test_returns_a_copy_not_the_live_dict(self):
        step_progress.set_step("obs1", "sequencer", "slew", ["slew"])

        snapshot = step_progress.get_all()
        snapshot["obs1"]["sequencer"]["step"] = "tampered"

        assert step_progress.get_all()["obs1"]["sequencer"]["step"] == "slew"
