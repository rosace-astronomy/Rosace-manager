"""
Tests for pipeline_loader.
Checks dynamic pipeline loading from program YAML files.
Run: pytest plugins/test_pipeline_loader.py -v
"""

import pytest
import tempfile
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parents[3]))

from rosace.plugins.pipeline_loader import (
    load_program_yaml,
    build_pipeline,
    hardware_sequencer_prefix,
    load_pipelines,
    load_sharing_plugin,
    list_programs,
    list_subplugins,
    validate_subplugin_catalogue,
    get_display_names,
)


class TestListProgrammes:

    def test_returns_list(self):
        result = list_programs()
        assert isinstance(result, list)

    def test_known_programs_present(self):
        """Only "demo" is a stable fixture (the packaged default) -- every
        other program is user-defined content, not something a test should
        hardcode as always present."""
        result = list_programs()
        assert "demo" in result

    def test_sorted_alphabetically(self):
        result = list_programs()
        assert result == sorted(result)


class TestLoadProgrammeYaml:

    def test_loads_demo(self):
        data = load_program_yaml("demo")
        assert "sequencer" in data
        assert "reduction" in data

    def test_sequencer_steps_are_list(self):
        data = load_program_yaml("demo")
        assert isinstance(data["sequencer"], list)
        assert len(data["sequencer"]) > 0

    def test_reduction_steps_are_list(self):
        data = load_program_yaml("demo")
        assert isinstance(data["reduction"], list)
        assert len(data["reduction"]) > 0

    def test_raises_on_unknown_program(self):
        with pytest.raises(FileNotFoundError, match="not found"):
            load_program_yaml("nonexistent_program")

    def test_error_message_lists_available(self):
        with pytest.raises(FileNotFoundError) as exc_info:
            load_program_yaml("xxx")
        assert "demo" in str(exc_info.value)

    @pytest.mark.parametrize("bad_name", ["../evil", "foo/bar", "foo bar", "..", ""])
    def test_raises_on_path_traversal_attempt(self, bad_name):
        """A name that could escape PROGRAMS_DIR is rejected before any path
        resolution is attempted — no FileNotFoundError that would reveal
        whether the traversed path exists or not."""
        with pytest.raises(ValueError, match="Invalid"):
            load_program_yaml(bad_name)


class TestBuildPipeline:
    """Dynamic resolution via importlib — no central registry, one import
    per declared step. The active backend (conf/config.yaml) determines the
    resolved SEQUENCER module; these tests run with the test config's real
    backend ("indi" — see conf/config.yaml), so no suffix."""

    def test_builds_full_sequencer_pipeline(self):
        pipeline = build_pipeline(["slew", "focus", "star_spectra_acquisition"], "sequencer")
        assert len(pipeline) == 3
        names = [p.__class__.__name__ for p in pipeline]
        assert names == ["SlewPlugin", "FocusPlugin", "StarSpectraAcquisitionPlugin"]

    def test_builds_partial_pipeline(self):
        """A pipeline can omit steps (e.g. no focus)."""
        pipeline = build_pipeline(["slew", "star_spectra_acquisition"], "sequencer")
        assert len(pipeline) == 2

    def test_raises_on_unknown_step(self):
        with pytest.raises(ValueError, match="inexistant"):
            build_pipeline(["slew", "inexistant"], "sequencer")

    def test_order_is_preserved(self):
        """The YAML's order is strictly honored."""
        pipeline = build_pipeline(["star_spectra_acquisition", "slew"], "sequencer")
        assert pipeline[0].__class__.__name__ == "StarSpectraAcquisitionPlugin"
        assert pipeline[1].__class__.__name__ == "SlewPlugin"

    def test_builds_reduction_pipeline(self):
        pipeline = build_pipeline(["master_bias", "master_dark", "master_flat", "extract"], "reduction")
        names = [p.__class__.__name__ for p in pipeline]
        assert names == ["MasterBiasPlugin", "MasterDarkPlugin", "MasterFlatPlugin", "ExtractPlugin"]

    def test_raises_on_unknown_kind(self):
        with pytest.raises(ValueError, match="bogus_kind"):
            build_pipeline(["slew"], "bogus_kind")

    @pytest.mark.parametrize("bad_name", ["../evil", "foo/bar", "foo bar", "foo-bar"])
    def test_raises_on_invalid_step_name(self, bad_name):
        """An invalid step name (program YAML) is rejected before any import
        is attempted — also protects names declared in a YAML, not just the
        program name itself."""
        with pytest.raises(ValueError, match="Invalid"):
            build_pipeline([bad_name], "sequencer")


class TestHardwareSequencerPrefix:
    """hardware_sequencer_prefix() -- the active hardware profile's own
    equipment-specific SEQUENCER steps (e.g. an AM3 mount's home/sync),
    prepended to every program's own sequencer list so a program YAML
    never needs to declare anything equipment-specific itself."""

    def test_empty_when_backend_is_not_indi(self, tmp_path, monkeypatch):
        from rosace.plugins import pipeline_loader
        import yaml as _yaml

        hw_yaml = tmp_path / "hw.yaml"
        hw_yaml.write_text(_yaml.dump({"sequencer_prefix": ["home_sync_am3"]}))
        monkeypatch.setattr(pipeline_loader, "HARDWARE_CONFIG_PATH", hw_yaml)
        monkeypatch.setattr(pipeline_loader, "SEQUENCER_BACKEND", "internal")

        assert hardware_sequencer_prefix() == []

    def test_empty_when_hardware_yaml_declares_no_prefix(self, tmp_path, monkeypatch):
        from rosace.plugins import pipeline_loader
        import yaml as _yaml

        hw_yaml = tmp_path / "hw.yaml"
        hw_yaml.write_text(_yaml.dump({"INDI_drivers": {}}))
        monkeypatch.setattr(pipeline_loader, "HARDWARE_CONFIG_PATH", hw_yaml)
        monkeypatch.setattr(pipeline_loader, "SEQUENCER_BACKEND", "indi")

        assert hardware_sequencer_prefix() == []

    def test_returns_the_declared_list(self, tmp_path, monkeypatch):
        from rosace.plugins import pipeline_loader
        import yaml as _yaml

        hw_yaml = tmp_path / "hw.yaml"
        hw_yaml.write_text(_yaml.dump({"sequencer_prefix": ["home_sync_am3"]}))
        monkeypatch.setattr(pipeline_loader, "HARDWARE_CONFIG_PATH", hw_yaml)
        monkeypatch.setattr(pipeline_loader, "SEQUENCER_BACKEND", "indi")

        assert hardware_sequencer_prefix() == ["home_sync_am3"]


class TestLoadPipelines:

    @pytest.fixture(autouse=True)
    def no_hardware_prefix(self, monkeypatch):
        """These tests resolve program YAMLs in isolation -- mock the
        active hardware profile's own prefix to empty so they don't
        implicitly depend on whatever the real default test config's
        simulator profile happens to declare (hardware_indi_simu.yaml
        gained its own sequencer_prefix, [guiding_dark_acquisition], on
        2026-09-20). test_prepends_hardware_sequencer_prefix below
        overrides this with its own explicit monkeypatch."""
        from rosace.plugins import pipeline_loader
        monkeypatch.setattr(pipeline_loader, "hardware_sequencer_prefix", lambda: [])

    def test_returns_tuple_of_two_lists(self):
        seq, red = load_pipelines("demo")
        assert isinstance(seq, list)
        assert isinstance(red, list)

    def test_prepends_hardware_sequencer_prefix(self, monkeypatch):
        """load_pipelines() prepends whatever hardware_sequencer_prefix()
        returns to the program's own sequencer list before building the
        pipeline -- this is what keeps equipment-specific steps (e.g. an AM3
        mount's home/sync) out of program YAMLs entirely."""
        from rosace.plugins import pipeline_loader
        monkeypatch.setattr(pipeline_loader, "hardware_sequencer_prefix", lambda: ["home_sync_am3"])

        seq, _ = load_pipelines("demo")

        names = [p.__class__.__name__ for p in seq]
        assert names[0] == "HomeSyncAm3Plugin"
        assert names == ["HomeSyncAm3Plugin", "SlewPlugin", "StarSpectraAcquisitionPlugin"]

    def test_bright_stars_no_focus(self):
        """No focus (bright star, manual adjustment); minimal reduction — the
        full chain lives in Be_stars.yaml."""
        seq, red = load_pipelines("bright_stars")
        seq_names = [p.__class__.__name__ for p in seq]
        red_names = [p.__class__.__name__ for p in red]
        assert "FocusPlugin" not in seq_names
        assert red_names == ["ExtractPlugin"]

    def test_nova_monitoring_minimal_sequencer(self):
        """Minimal sequencer (no focus, maximum responsiveness); minimal reduction."""
        seq, red = load_pipelines("nova_monitoring")
        seq_names = [p.__class__.__name__ for p in seq]
        red_names = [p.__class__.__name__ for p in red]
        assert "FocusPlugin" not in seq_names
        assert len(seq) == 2
        assert red_names == ["ExtractPlugin"]

    def test_demo_full_pipeline(self):
        seq, red = load_pipelines("demo")
        seq_names = [p.__class__.__name__ for p in seq]
        red_names = [p.__class__.__name__ for p in red]
        assert seq_names == ["SlewPlugin", "StarSpectraAcquisitionPlugin"]
        assert red_names == ["ExtractPlugin"]

    def test_be_stars_reduction_chain_resolves(self):
        """Every step of Be_stars.yaml's reduction chain resolves to a real
        sub-plugin, in the declared order. Built from the reduction list alone:
        the program's sequencer steps need the indi backend."""
        import yaml as _yaml
        from rosace.plugins import pipeline_loader

        program = _yaml.safe_load((pipeline_loader.PROGRAMS_DIR / "Be_stars.yaml").read_text())
        pipeline = build_pipeline(program["reduction"], "reduction")

        assert len(pipeline) == len(program["reduction"])
        assert program["reduction"][:2] == ["calculate_flux", "master_bias"]
        assert program["reduction"][-3:] == ["write_spectrum", "plot_spectrum", "export_demetra"]

    def test_raises_on_unknown_program(self):
        with pytest.raises(FileNotFoundError):
            load_pipelines("programme_inexistant")

    def test_custom_yaml_program(self, tmp_path, monkeypatch):
        """A custom YAML is loaded correctly."""
        import yaml as _yaml
        from rosace.plugins import pipeline_loader

        custom_yaml = tmp_path / "test_custom.yaml"
        custom_yaml.write_text(_yaml.dump({
            "program": "test_custom",
            "sequencer": ["slew", "star_spectra_acquisition"],
            "reduction": ["master_bias", "extract"],
        }))

        monkeypatch.setattr(pipeline_loader, "PROGRAMS_DIR", tmp_path)
        seq, red = load_pipelines("test_custom")
        assert len(seq) == 2
        assert len(red) == 2

    def test_raises_if_sequencer_key_missing(self, tmp_path, monkeypatch):
        import yaml as _yaml
        from rosace.plugins import pipeline_loader

        bad_yaml = tmp_path / "bad.yaml"
        bad_yaml.write_text(_yaml.dump({"reduction": ["extract"]}))
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
    """_resolve_module_and_class() picks the SEQUENCER module based on
    SEQUENCER_BACKEND (config.py) — REDUCTION has no hardware dependency,
    never suffixed regardless of the active backend."""

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

        module_path, class_name = pipeline_loader._resolve_module_and_class("reduction", "extract")

        assert module_path == "rosace.plugins.reduction_plugins.extract.extract"

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
    """load_sharing_plugin() resolves a single sub-plugin (singular "sharing"
    key in the YAML) — not a list like sequencer/reduction."""

    def test_loads_blank_for_demo(self):
        sub = load_sharing_plugin("demo")
        assert sub.__class__.__name__ == "BlankSharePlugin"

    def test_raises_if_sharing_key_missing(self, tmp_path, monkeypatch):
        import yaml as _yaml
        from rosace.plugins import pipeline_loader

        bad_yaml = tmp_path / "bad3.yaml"
        bad_yaml.write_text(_yaml.dump({"sequencer": ["slew"], "reduction": ["extract"]}))
        monkeypatch.setattr(pipeline_loader, "PROGRAMS_DIR", tmp_path)

        with pytest.raises(KeyError, match="sharing"):
            load_sharing_plugin("bad3")

    def test_raises_on_unknown_sharing_subplugin(self, tmp_path, monkeypatch):
        import yaml as _yaml
        from rosace.plugins import pipeline_loader

        bad_yaml = tmp_path / "bad4.yaml"
        bad_yaml.write_text(_yaml.dump({
            "sequencer": ["slew"], "reduction": ["extract"], "sharing": "inexistant",
        }))
        monkeypatch.setattr(pipeline_loader, "PROGRAMS_DIR", tmp_path)

        with pytest.raises(ValueError, match="inexistant"):
            load_sharing_plugin("bad4")

    def test_raises_on_invalid_sharing_name(self, tmp_path, monkeypatch):
        import yaml as _yaml
        from rosace.plugins import pipeline_loader

        bad_yaml = tmp_path / "bad5.yaml"
        bad_yaml.write_text(_yaml.dump({
            "sequencer": ["slew"], "reduction": ["extract"], "sharing": "../evil",
        }))
        monkeypatch.setattr(pipeline_loader, "PROGRAMS_DIR", tmp_path)

        with pytest.raises(ValueError, match="Invalid"):
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
            "sequencer": self._make_family(tmp_path, "sequencer", ["slew", "pointing_72ed"]),
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


class TestGetDisplayNames:
    """get_display_names() -- backs GET /subplugins/display-names, used to
    label step-progress chips in the GUI."""

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

    def test_reads_display_name_constant(self, tmp_path, monkeypatch):
        from rosace.plugins import pipeline_loader

        monkeypatch.setattr(pipeline_loader, "_SUBPLUGIN_FAMILY_DIRS", {
            "sequencer": self._make_family(tmp_path, "sequencer", {
                "foo": '"""Docstring."""\n\nDISPLAY_NAME = "Foo"\n',
            }),
        })

        assert pipeline_loader.get_display_names(["sequencer"]) == {"sequencer": {"foo": "Foo"}}

    def test_falls_back_to_name_when_display_name_missing(self, tmp_path, monkeypatch):
        from rosace.plugins import pipeline_loader

        monkeypatch.setattr(pipeline_loader, "_SUBPLUGIN_FAMILY_DIRS", {
            "sequencer": self._make_family(tmp_path, "sequencer", {"foo": '"""Docstring."""\n'}),
        })

        assert pipeline_loader.get_display_names(["sequencer"]) == {"sequencer": {"foo": "foo"}}

    def test_falls_back_to_name_when_canonical_module_missing(self, tmp_path, monkeypatch):
        from rosace.plugins import pipeline_loader

        monkeypatch.setattr(pipeline_loader, "_SUBPLUGIN_FAMILY_DIRS", {
            "sequencer": self._make_family(tmp_path, "sequencer", {"foo": None}),
        })

        assert pipeline_loader.get_display_names(["sequencer"]) == {"sequencer": {"foo": "foo"}}

    def test_ignores_non_string_constant(self, tmp_path, monkeypatch):
        from rosace.plugins import pipeline_loader

        monkeypatch.setattr(pipeline_loader, "_SUBPLUGIN_FAMILY_DIRS", {
            "sequencer": self._make_family(tmp_path, "sequencer", {
                "foo": '"""Docstring."""\n\nDISPLAY_NAME = 42\n',
            }),
        })

        assert pipeline_loader.get_display_names(["sequencer"]) == {"sequencer": {"foo": "foo"}}

    def test_families_filter_restricts_output(self):
        assert set(get_display_names(["reduction"])) == {"reduction"}

    def test_no_filter_covers_all_three_families(self):
        assert set(get_display_names()) == {"sequencer", "reduction", "sharing"}


class TestSubpluginDisplayNames:
    """Not a startup check, same posture as TestSubpluginDocstrings above --
    a missing DISPLAY_NAME degrades a GUI label, it isn't a correctness
    risk. Runs against the real, on-disk catalogue specifically to catch a
    future sub-plugin added without one."""

    def test_every_sequencer_and_reduction_subplugin_has_a_display_name(self):
        names = get_display_names(["sequencer", "reduction"])
        missing = [
            f"{family}/{name}"
            for family, name, _ in list_subplugins(["sequencer", "reduction"])
            if names[family][name] == name  # the "no DISPLAY_NAME" fallback
        ]
        assert not missing, f"Sub-plugin(s) missing a DISPLAY_NAME: {missing}"

    def test_internal_backend_declares_same_display_name_as_canonical(self):
        """slew and star_spectra_acquisition each have both a canonical and
        an _internal.py file, representing one logical step -- the GUI must
        show the same label no matter which SEQUENCER_BACKEND is active.
        (focus used to be a third one, back when it was a science-camera
        autofocus placeholder -- it's now a guiding-camera focus-quality
        measurement, indi-only, no _internal.py counterpart.)"""
        from rosace.plugins.pipeline_loader import (
            _SUBPLUGIN_FAMILY_DIRS,
            _module_display_name,
            _read_display_name_constant,
        )

        sequencer_dir = _SUBPLUGIN_FAMILY_DIRS["sequencer"]
        mismatched = []
        for name in ("slew", "star_spectra_acquisition"):
            canonical = _module_display_name(sequencer_dir, name)
            internal = _read_display_name_constant(sequencer_dir / name / f"{name}_internal.py")
            if canonical != internal:
                mismatched.append(f"{name}: canonical={canonical!r} internal={internal!r}")
        assert not mismatched, f"DISPLAY_NAME mismatch between backend variants: {mismatched}"
