"""
Tests de observation_file.py — persistance YAML de l'observation.
Lancement : pytest test_observation_file.py -v
"""

from datetime import datetime, timezone

import yaml

from rosace.models import Observation, Target
from rosace.observation_file import create_observation_file, write_observation_file


def _make_obs(obs_dir=None) -> Observation:
    return Observation(
        id="obs12345",
        created_at=datetime.now(timezone.utc),
        session_id="sess1234",
        obs_dir=str(obs_dir) if obs_dir is not None else None,
    )


class TestCreateObservationFile:

    def test_sets_yaml_file_under_target_dir(self, tmp_path):
        obs = _make_obs(tmp_path)
        obs.scheduler_data["target"] = Target(name="Vega", ra=1.0, dec=1.0)

        filepath = create_observation_file(obs, tmp_path)

        assert filepath.parent == tmp_path
        assert obs.yaml_file == filepath.name

    def test_does_not_write_to_disk(self, tmp_path):
        """create_observation_file fixe juste le nom, n'écrit rien : c'est write_observation_file qui écrit."""
        obs = _make_obs(tmp_path)
        filepath = create_observation_file(obs, tmp_path)
        assert not filepath.exists()


class TestWriteObservationFile:

    def test_does_nothing_if_yaml_file_not_set(self, tmp_path):
        obs = _make_obs(tmp_path)  # yaml_file toujours None
        write_observation_file(obs)  # ne doit pas lever d'erreur
        assert list(tmp_path.iterdir()) == []

    def test_writes_full_observation_state(self, tmp_path):
        obs = _make_obs(tmp_path)
        obs.scheduler_data["target"] = Target(name="Vega", ra=279.23, dec=38.78)
        create_observation_file(obs, tmp_path)

        write_observation_file(obs)

        with open(tmp_path / obs.yaml_file, encoding="utf-8") as f:
            content = yaml.safe_load(f)

        assert content["id"] == "obs12345"
        assert content["session_id"] == "sess1234"
        assert content["scheduler_data"]["target"]["name"] == "Vega"

    def test_rewriting_reflects_new_data(self, tmp_path):
        """Le fichier grandit à chaque réécriture — pas un simple ajout, un instantané complet."""
        obs = _make_obs(tmp_path)
        create_observation_file(obs, tmp_path)
        write_observation_file(obs)

        obs.sequencer_data["raw_files"] = ["spectrum_0001.fits"]
        write_observation_file(obs)

        with open(tmp_path / obs.yaml_file, encoding="utf-8") as f:
            content = yaml.safe_load(f)
        assert content["sequencer_data"]["raw_files"] == ["spectrum_0001.fits"]
