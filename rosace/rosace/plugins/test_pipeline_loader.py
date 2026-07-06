"""
Tests du pipeline_loader.
Vérifie le chargement dynamique des pipelines depuis les fichiers YAML de programme.
Lancez avec : pytest plugins/test_pipeline_loader.py -v
"""

import pytest
import tempfile
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parents[3]))

from rosace.plugins.pipeline_loader import (
    load_programme_yaml,
    build_pipeline,
    load_pipelines,
    list_programmes,
    _build_registry,
)


class TestListProgrammes:

    def test_returns_list(self):
        result = list_programmes()
        assert isinstance(result, list)

    def test_known_programmes_present(self):
        result = list_programmes()
        assert "etoiles_be" in result
        assert "etoiles_brillantes" in result
        assert "nova_monitoring" in result

    def test_sorted_alphabetically(self):
        result = list_programmes()
        assert result == sorted(result)


class TestLoadProgrammeYaml:

    def test_loads_etoiles_be(self):
        data = load_programme_yaml("etoiles_be")
        assert "sequencer" in data
        assert "reduction" in data

    def test_sequencer_steps_are_list(self):
        data = load_programme_yaml("etoiles_be")
        assert isinstance(data["sequencer"], list)
        assert len(data["sequencer"]) > 0

    def test_reduction_steps_are_list(self):
        data = load_programme_yaml("etoiles_be")
        assert isinstance(data["reduction"], list)
        assert len(data["reduction"]) > 0

    def test_raises_on_unknown_programme(self):
        with pytest.raises(FileNotFoundError, match="introuvable"):
            load_programme_yaml("programme_inexistant")

    def test_error_message_lists_available(self):
        with pytest.raises(FileNotFoundError) as exc_info:
            load_programme_yaml("xxx")
        assert "etoiles_be" in str(exc_info.value)


class TestBuildPipeline:

    def test_builds_full_sequencer_pipeline(self):
        registry = _build_registry()
        pipeline = build_pipeline(["slew", "focus", "expose"], registry)
        assert len(pipeline) == 3
        names = [p.__class__.__name__ for p in pipeline]
        assert names == ["SlewPlugin", "FocusPlugin", "ExposePlugin"]

    def test_builds_partial_pipeline(self):
        """Un pipeline peut omettre des étapes (ex: pas de focus)."""
        registry = _build_registry()
        pipeline = build_pipeline(["slew", "expose"], registry)
        assert len(pipeline) == 2

    def test_raises_on_unknown_step(self):
        registry = _build_registry()
        with pytest.raises(ValueError, match="inexistant"):
            build_pipeline(["slew", "inexistant"], registry)

    def test_error_message_lists_valid_names(self):
        registry = _build_registry()
        with pytest.raises(ValueError) as exc_info:
            build_pipeline(["xxx"], registry)
        assert "slew" in str(exc_info.value)

    def test_order_is_preserved(self):
        """L'ordre du YAML est strictement respecté."""
        registry = _build_registry()
        pipeline = build_pipeline(["expose", "slew"], registry)
        assert pipeline[0].__class__.__name__ == "ExposePlugin"
        assert pipeline[1].__class__.__name__ == "SlewPlugin"


class TestLoadPipelines:

    def test_returns_tuple_of_two_lists(self):
        seq, red = load_pipelines("etoiles_be")
        assert isinstance(seq, list)
        assert isinstance(red, list)

    def test_etoiles_be_full_pipeline(self):
        seq, red = load_pipelines("etoiles_be")
        seq_names = [p.__class__.__name__ for p in seq]
        red_names = [p.__class__.__name__ for p in red]
        assert seq_names == ["SlewPlugin", "FocusPlugin", "ExposePlugin"]
        assert red_names == ["BiasPlugin", "DarkPlugin", "FlatPlugin", "ExtractPlugin"]

    def test_etoiles_brillantes_no_focus_no_dark(self):
        seq, red = load_pipelines("etoiles_brillantes")
        seq_names = [p.__class__.__name__ for p in seq]
        red_names = [p.__class__.__name__ for p in red]
        assert "FocusPlugin" not in seq_names
        assert "DarkPlugin" not in red_names

    def test_nova_monitoring_minimal(self):
        seq, red = load_pipelines("nova_monitoring")
        seq_names = [p.__class__.__name__ for p in seq]
        red_names = [p.__class__.__name__ for p in red]
        assert "FocusPlugin" not in seq_names
        assert "FlatPlugin" not in red_names
        assert len(seq) == 2
        assert len(red) == 2

    def test_raises_on_unknown_programme(self):
        with pytest.raises(FileNotFoundError):
            load_pipelines("programme_inexistant")

    def test_custom_yaml_programme(self, tmp_path, monkeypatch):
        """Un YAML personnalisé est correctement chargé."""
        import yaml as _yaml
        from rosace.plugins import pipeline_loader

        custom_yaml = tmp_path / "test_custom.yaml"
        custom_yaml.write_text(_yaml.dump({
            "programme": "test_custom",
            "sequencer": ["slew", "expose"],
            "reduction": ["bias", "extract"],
        }))

        monkeypatch.setattr(pipeline_loader, "PROGRAMMES_DIR", tmp_path)
        seq, red = load_pipelines("test_custom")
        assert len(seq) == 2
        assert len(red) == 2

    def test_raises_if_sequencer_key_missing(self, tmp_path, monkeypatch):
        import yaml as _yaml
        from rosace.plugins import pipeline_loader

        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text(_yaml.dump({"reduction": ["bias"]}))
        monkeypatch.setattr(pipeline_loader, "PROGRAMMES_DIR", tmp_path)

        with pytest.raises(KeyError, match="sequencer"):
            load_pipelines("bad")

    def test_raises_if_reduction_key_missing(self, tmp_path, monkeypatch):
        import yaml as _yaml
        from rosace.plugins import pipeline_loader

        bad_yaml = tmp_path / "bad2.yaml"
        bad_yaml.write_text(_yaml.dump({"sequencer": ["slew"]}))
        monkeypatch.setattr(pipeline_loader, "PROGRAMMES_DIR", tmp_path)

        with pytest.raises(KeyError, match="reduction"):
            load_pipelines("bad2")
