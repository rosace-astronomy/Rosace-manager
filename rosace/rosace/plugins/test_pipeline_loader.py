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
    load_sharing_plugin,
    list_programmes,
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
        assert "demo" in result

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
    """Résolution dynamique par importlib — pas de registre central, un import
    par étape déclarée. Le backend actif (conf/config.yaml) détermine le
    module SEQUENCER résolu ; ces tests tournent avec le backend réel de la
    config de test ("indi" — voir conf/config.yaml), donc sans suffixe."""

    def test_builds_full_sequencer_pipeline(self):
        pipeline = build_pipeline(["slew", "focus", "expose"], "sequencer")
        assert len(pipeline) == 3
        names = [p.__class__.__name__ for p in pipeline]
        assert names == ["SlewPlugin", "FocusPlugin", "ExposePlugin"]

    def test_builds_partial_pipeline(self):
        """Un pipeline peut omettre des étapes (ex: pas de focus)."""
        pipeline = build_pipeline(["slew", "expose"], "sequencer")
        assert len(pipeline) == 2

    def test_raises_on_unknown_step(self):
        with pytest.raises(ValueError, match="inexistant"):
            build_pipeline(["slew", "inexistant"], "sequencer")

    def test_order_is_preserved(self):
        """L'ordre du YAML est strictement respecté."""
        pipeline = build_pipeline(["expose", "slew"], "sequencer")
        assert pipeline[0].__class__.__name__ == "ExposePlugin"
        assert pipeline[1].__class__.__name__ == "SlewPlugin"

    def test_builds_reduction_pipeline(self):
        pipeline = build_pipeline(["bias", "dark", "flat", "extract"], "reduction")
        names = [p.__class__.__name__ for p in pipeline]
        assert names == ["BiasPlugin", "DarkPlugin", "FlatPlugin", "ExtractPlugin"]

    def test_raises_on_unknown_kind(self):
        with pytest.raises(ValueError, match="bogus_kind"):
            build_pipeline(["slew"], "bogus_kind")


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

    def test_etoiles_brillantes_no_focus_full_reduction(self):
        """Pas de focus (étoile brillante, réglage manuel) — mais la chaîne de
        réduction reste complète : chaque sous-plugin REDUCTION dépend
        explicitement du précédent, aucune étape ne peut être sautée."""
        seq, red = load_pipelines("etoiles_brillantes")
        seq_names = [p.__class__.__name__ for p in seq]
        red_names = [p.__class__.__name__ for p in red]
        assert "FocusPlugin" not in seq_names
        assert red_names == ["BiasPlugin", "DarkPlugin", "FlatPlugin", "ExtractPlugin"]

    def test_nova_monitoring_minimal_sequencer_full_reduction(self):
        """Séquenceur minimal (pas de focus, réactivité maximale) — mais la
        chaîne de réduction reste complète, même contrainte que ci-dessus."""
        seq, red = load_pipelines("nova_monitoring")
        seq_names = [p.__class__.__name__ for p in seq]
        red_names = [p.__class__.__name__ for p in red]
        assert "FocusPlugin" not in seq_names
        assert len(seq) == 2
        assert red_names == ["BiasPlugin", "DarkPlugin", "FlatPlugin", "ExtractPlugin"]

    def test_demo_full_pipeline(self):
        seq, red = load_pipelines("demo")
        seq_names = [p.__class__.__name__ for p in seq]
        red_names = [p.__class__.__name__ for p in red]
        assert seq_names == ["SlewPlugin", "FocusPlugin", "ExposePlugin"]
        assert red_names == ["BiasPlugin", "DarkPlugin", "FlatPlugin", "ExtractPlugin"]

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


class TestSequencerBackendDispatch:
    """_resolve_module_and_class() choisit le module SEQUENCER selon
    SEQUENCER_BACKEND (config.py) — REDUCTION n'a pas de dépendance
    matérielle, jamais suffixé quel que soit le backend actif."""

    def test_internal_backend_resolves_internal_suffix(self, monkeypatch):
        from rosace.plugins import pipeline_loader
        monkeypatch.setattr(pipeline_loader, "SEQUENCER_BACKEND", "internal")

        module_path, class_name = pipeline_loader._resolve_module_and_class("sequencer", "slew")

        assert module_path == "rosace.plugins.sequencer_plugins.slew.slew_internal"
        assert class_name == "SlewPlugin"

    def test_indi_backend_resolves_no_suffix(self, monkeypatch):
        from rosace.plugins import pipeline_loader
        monkeypatch.setattr(pipeline_loader, "SEQUENCER_BACKEND", "indi")

        module_path, class_name = pipeline_loader._resolve_module_and_class("sequencer", "slew")

        assert module_path == "rosace.plugins.sequencer_plugins.slew.slew"

    def test_reduction_never_suffixed_regardless_of_backend(self, monkeypatch):
        from rosace.plugins import pipeline_loader
        monkeypatch.setattr(pipeline_loader, "SEQUENCER_BACKEND", "internal")

        module_path, class_name = pipeline_loader._resolve_module_and_class("reduction", "bias")

        assert module_path == "rosace.plugins.reduction_plugins.bias.bias"

    def test_unknown_backend_raises_value_error(self, monkeypatch):
        from rosace.plugins import pipeline_loader
        monkeypatch.setattr(pipeline_loader, "SEQUENCER_BACKEND", "bogus")

        with pytest.raises(ValueError, match="bogus"):
            pipeline_loader._resolve_module_and_class("sequencer", "slew")

    def test_internal_backend_actually_loads_internal_class(self, monkeypatch):
        from rosace.plugins import pipeline_loader
        monkeypatch.setattr(pipeline_loader, "SEQUENCER_BACKEND", "internal")

        pipeline = pipeline_loader.build_pipeline(["slew"], "sequencer")

        assert pipeline[0].__class__.__module__ == "rosace.plugins.sequencer_plugins.slew.slew_internal"

    def test_indi_backend_actually_loads_canonical_class(self, monkeypatch):
        from rosace.plugins import pipeline_loader
        monkeypatch.setattr(pipeline_loader, "SEQUENCER_BACKEND", "indi")

        pipeline = pipeline_loader.build_pipeline(["slew"], "sequencer")

        assert pipeline[0].__class__.__module__ == "rosace.plugins.sequencer_plugins.slew.slew"


class TestLoadSharingPlugin:
    """load_sharing_plugin() résout un seul sous-plugin (clé "sharing"
    singulière dans le YAML) — pas une liste comme sequencer/reduction."""

    def test_loads_blank_for_demo(self):
        sub = load_sharing_plugin("demo")
        assert sub.__class__.__name__ == "BlankPlugin"

    def test_raises_if_sharing_key_missing(self, tmp_path, monkeypatch):
        import yaml as _yaml
        from rosace.plugins import pipeline_loader

        bad_yaml = tmp_path / "bad3.yaml"
        bad_yaml.write_text(_yaml.dump({"sequencer": ["slew"], "reduction": ["bias"]}))
        monkeypatch.setattr(pipeline_loader, "PROGRAMMES_DIR", tmp_path)

        with pytest.raises(KeyError, match="sharing"):
            load_sharing_plugin("bad3")

    def test_raises_on_unknown_sharing_subplugin(self, tmp_path, monkeypatch):
        import yaml as _yaml
        from rosace.plugins import pipeline_loader

        bad_yaml = tmp_path / "bad4.yaml"
        bad_yaml.write_text(_yaml.dump({
            "sequencer": ["slew"], "reduction": ["bias"], "sharing": "inexistant",
        }))
        monkeypatch.setattr(pipeline_loader, "PROGRAMMES_DIR", tmp_path)

        with pytest.raises(ValueError, match="inexistant"):
            load_sharing_plugin("bad4")
