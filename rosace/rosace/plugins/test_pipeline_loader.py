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
    load_program_yaml,
    build_pipeline,
    load_pipelines,
    load_sharing_plugin,
    list_programs,
    list_subplugins,
    validate_subplugin_catalogue,
)


class TestListProgrammes:

    def test_returns_list(self):
        result = list_programs()
        assert isinstance(result, list)

    def test_known_programs_present(self):
        result = list_programs()
        assert "etoiles_be" in result
        assert "etoiles_brillantes" in result
        assert "nova_monitoring" in result
        assert "demo" in result

    def test_sorted_alphabetically(self):
        result = list_programs()
        assert result == sorted(result)


class TestLoadProgrammeYaml:

    def test_loads_etoiles_be(self):
        data = load_program_yaml("etoiles_be")
        assert "sequencer" in data
        assert "reduction" in data

    def test_sequencer_steps_are_list(self):
        data = load_program_yaml("etoiles_be")
        assert isinstance(data["sequencer"], list)
        assert len(data["sequencer"]) > 0

    def test_reduction_steps_are_list(self):
        data = load_program_yaml("etoiles_be")
        assert isinstance(data["reduction"], list)
        assert len(data["reduction"]) > 0

    def test_raises_on_unknown_program(self):
        with pytest.raises(FileNotFoundError, match="introuvable"):
            load_program_yaml("programme_inexistant")

    def test_error_message_lists_available(self):
        with pytest.raises(FileNotFoundError) as exc_info:
            load_program_yaml("xxx")
        assert "etoiles_be" in str(exc_info.value)

    @pytest.mark.parametrize("bad_name", ["../evil", "foo/bar", "foo bar", "..", ""])
    def test_raises_on_path_traversal_attempt(self, bad_name):
        """Un nom qui pourrait s'échapper de PROGRAMS_DIR est rejeté avant
        toute résolution de chemin — pas de FileNotFoundError qui révèlerait
        si le chemin traversé existe ou non."""
        with pytest.raises(ValueError, match="invalide"):
            load_program_yaml(bad_name)


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

    @pytest.mark.parametrize("bad_name", ["../evil", "foo/bar", "foo bar", "foo-bar"])
    def test_raises_on_invalid_step_name(self, bad_name):
        """Un nom d'étape invalide (YAML programme) est rejeté avant toute
        tentative d'import — protège aussi les noms déclarés dans un YAML,
        pas seulement le nom de programme lui-même."""
        with pytest.raises(ValueError, match="invalide"):
            build_pipeline([bad_name], "sequencer")


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

    def test_raises_on_unknown_program(self):
        with pytest.raises(FileNotFoundError):
            load_pipelines("programme_inexistant")

    def test_custom_yaml_program(self, tmp_path, monkeypatch):
        """Un YAML personnalisé est correctement chargé."""
        import yaml as _yaml
        from rosace.plugins import pipeline_loader

        custom_yaml = tmp_path / "test_custom.yaml"
        custom_yaml.write_text(_yaml.dump({
            "program": "test_custom",
            "sequencer": ["slew", "expose"],
            "reduction": ["bias", "extract"],
        }))

        monkeypatch.setattr(pipeline_loader, "PROGRAMS_DIR", tmp_path)
        seq, red = load_pipelines("test_custom")
        assert len(seq) == 2
        assert len(red) == 2

    def test_raises_if_sequencer_key_missing(self, tmp_path, monkeypatch):
        import yaml as _yaml
        from rosace.plugins import pipeline_loader

        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text(_yaml.dump({"reduction": ["bias"]}))
        monkeypatch.setattr(pipeline_loader, "PROGRAMS_DIR", tmp_path)

        with pytest.raises(KeyError, match="sequencer"):
            load_pipelines("bad")

    def test_raises_if_reduction_key_missing(self, tmp_path, monkeypatch):
        import yaml as _yaml
        from rosace.plugins import pipeline_loader

        bad_yaml = tmp_path / "bad2.yaml"
        bad_yaml.write_text(_yaml.dump({"sequencer": ["slew"]}))
        monkeypatch.setattr(pipeline_loader, "PROGRAMS_DIR", tmp_path)

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
        assert sub.__class__.__name__ == "BlankSharePlugin"

    def test_raises_if_sharing_key_missing(self, tmp_path, monkeypatch):
        import yaml as _yaml
        from rosace.plugins import pipeline_loader

        bad_yaml = tmp_path / "bad3.yaml"
        bad_yaml.write_text(_yaml.dump({"sequencer": ["slew"], "reduction": ["bias"]}))
        monkeypatch.setattr(pipeline_loader, "PROGRAMS_DIR", tmp_path)

        with pytest.raises(KeyError, match="sharing"):
            load_sharing_plugin("bad3")

    def test_raises_on_unknown_sharing_subplugin(self, tmp_path, monkeypatch):
        import yaml as _yaml
        from rosace.plugins import pipeline_loader

        bad_yaml = tmp_path / "bad4.yaml"
        bad_yaml.write_text(_yaml.dump({
            "sequencer": ["slew"], "reduction": ["bias"], "sharing": "inexistant",
        }))
        monkeypatch.setattr(pipeline_loader, "PROGRAMS_DIR", tmp_path)

        with pytest.raises(ValueError, match="inexistant"):
            load_sharing_plugin("bad4")

    def test_raises_on_invalid_sharing_name(self, tmp_path, monkeypatch):
        import yaml as _yaml
        from rosace.plugins import pipeline_loader

        bad_yaml = tmp_path / "bad5.yaml"
        bad_yaml.write_text(_yaml.dump({
            "sequencer": ["slew"], "reduction": ["bias"], "sharing": "../evil",
        }))
        monkeypatch.setattr(pipeline_loader, "PROGRAMS_DIR", tmp_path)

        with pytest.raises(ValueError, match="invalide"):
            load_sharing_plugin("bad5")


class TestValidateSubpluginCatalogue:
    """validate_subplugin_catalogue() — startup check for the real, on-disk sub-plugin
    directories. Fake families are built under tmp_path per test so each case is
    isolated from the real catalogue (which is also exercised for free: every other
    test in this file only runs because plugins/__init__.py already calls this
    function successfully against the real directories at import time)."""

    @staticmethod
    def _make_family(root: Path, family: str, subplugin_names: list[str]) -> Path:
        family_dir = root / family
        family_dir.mkdir(parents=True)
        for name in subplugin_names:
            (family_dir / name).mkdir()
            (family_dir / name / "__init__.py").touch()
        return family_dir

    def test_passes_on_distinct_lowercase_names(self, tmp_path, monkeypatch):
        from rosace.plugins import pipeline_loader

        monkeypatch.setattr(pipeline_loader, "_SUBPLUGIN_FAMILY_DIRS", {
            "sequencer": self._make_family(tmp_path, "sequencer", ["slew", "pointing_72ed_01"]),
            "reduction": self._make_family(tmp_path, "reduction", ["bias"]),
            "sharing":   self._make_family(tmp_path, "sharing", ["blank_share"]),
        })

        validate_subplugin_catalogue()  # must not raise

    def test_raises_on_uppercase_directory_name(self, tmp_path, monkeypatch):
        from rosace.plugins import pipeline_loader

        monkeypatch.setattr(pipeline_loader, "_SUBPLUGIN_FAMILY_DIRS", {
            "sequencer": self._make_family(tmp_path, "sequencer", ["Pointing_72ED_01"]),
            "reduction": self._make_family(tmp_path, "reduction", []),
            "sharing":   self._make_family(tmp_path, "sharing", []),
        })

        with pytest.raises(RuntimeError, match="not all-lowercase"):
            validate_subplugin_catalogue()

    def test_raises_on_cross_family_name_collision(self, tmp_path, monkeypatch):
        from rosace.plugins import pipeline_loader

        monkeypatch.setattr(pipeline_loader, "_SUBPLUGIN_FAMILY_DIRS", {
            "sequencer": self._make_family(tmp_path, "sequencer", ["dark"]),
            "reduction": self._make_family(tmp_path, "reduction", ["dark"]),
            "sharing":   self._make_family(tmp_path, "sharing", []),
        })

        with pytest.raises(RuntimeError, match="collision") as exc_info:
            validate_subplugin_catalogue()
        assert "'dark' exists in both sequencer and reduction" in str(exc_info.value)


class TestListSubplugins:
    """list_subplugins() -- discoverability tool, also exposed via
    `python -m rosace.main --list-subplugins [family]`."""

    @staticmethod
    def _make_family(root: Path, family: str, files_by_name: dict) -> Path:
        """files_by_name: {sub-plugin name: <name>.py source, or None to
        skip writing that file entirely (missing-module case)}."""
        family_dir = root / family
        family_dir.mkdir(parents=True)
        for name, source in files_by_name.items():
            sub_dir = family_dir / name
            sub_dir.mkdir()
            (sub_dir / "__init__.py").touch()
            if source is not None:
                (sub_dir / f"{name}.py").write_text(source)
        return family_dir

    def test_returns_known_real_subplugin(self):
        rows = list_subplugins(["sequencer"])
        names = {name for _, name, _ in rows}
        assert "slew" in names

    def test_summary_is_first_line_of_module_docstring(self, tmp_path, monkeypatch):
        from rosace.plugins import pipeline_loader

        monkeypatch.setattr(pipeline_loader, "_SUBPLUGIN_FAMILY_DIRS", {
            "sequencer": self._make_family(tmp_path, "sequencer", {
                "foo": '"""One-line summary.\n\nMore detail below, not part of the summary.\n"""\n',
            }),
        })

        rows = pipeline_loader.list_subplugins()

        assert rows == [("sequencer", "foo", "One-line summary.")]

    def test_missing_docstring_reported_explicitly_not_skipped(self, tmp_path, monkeypatch):
        from rosace.plugins import pipeline_loader

        monkeypatch.setattr(pipeline_loader, "_SUBPLUGIN_FAMILY_DIRS", {
            "sequencer": self._make_family(tmp_path, "sequencer", {"foo": "x = 1\n"}),
        })

        rows = pipeline_loader.list_subplugins()

        assert rows == [("sequencer", "foo", "(no docstring)")]

    def test_missing_canonical_module_reported_explicitly(self, tmp_path, monkeypatch):
        """A sub-plugin dir with no <name>.py at all (e.g. sequencer-only,
        internal-backend-only during early development) -- still listed,
        not silently dropped."""
        from rosace.plugins import pipeline_loader

        monkeypatch.setattr(pipeline_loader, "_SUBPLUGIN_FAMILY_DIRS", {
            "sequencer": self._make_family(tmp_path, "sequencer", {"foo": None}),
        })

        rows = pipeline_loader.list_subplugins()

        assert rows == [("sequencer", "foo", "(no docstring)")]

    def test_families_filter_restricts_output(self):
        rows = list_subplugins(["reduction"])
        assert rows and all(family == "reduction" for family, _, _ in rows)

    def test_no_filter_covers_all_three_families(self):
        rows = list_subplugins()
        assert {family for family, _, _ in rows} == {"sequencer", "reduction", "sharing"}

    def test_sorted_by_pipeline_order_then_name(self):
        """Family order is sequencer, reduction, sharing -- the order they
        actually run in -- not alphabetical (which would put reduction
        first). Within a family, names are alphabetical."""
        rows = list_subplugins()

        families_in_order = [family for family, _, _ in rows]
        first_reduction = families_in_order.index("reduction")
        first_sharing = families_in_order.index("sharing")
        assert families_in_order[:first_reduction] == ["sequencer"] * first_reduction
        assert all(f == "reduction" for f in families_in_order[first_reduction:first_sharing])
        assert all(f == "sharing" for f in families_in_order[first_sharing:])

        for family in ("sequencer", "reduction", "sharing"):
            names = [name for f, name, _ in rows if f == family]
            assert names == sorted(names)


class TestSubpluginDocstrings:
    """Not a startup check (see TestValidateSubpluginCatalogue above) -- a
    missing module docstring isn't a runtime correctness risk, so it
    shouldn't cost every real deployment a startup check. Runs against the
    real, on-disk catalogue (unlike TestListSubplugins' fake tmp_path
    families above) specifically to catch a future sub-plugin added
    without one."""

    def test_every_real_subplugin_has_a_non_empty_module_docstring(self):
        missing = [
            f"{family}/{name}"
            for family, name, summary in list_subplugins()
            if summary == "(no docstring)"
        ]
        assert not missing, f"Sub-plugin(s) missing a module docstring: {missing}"
