"""
Tests for the EXPORT_DEMETRA sub-plugin (ExportDemetraPlugin).
Run: pytest plugins/reduction_plugins/export_demetra/test_export_demetra.py -v
"""

import zipfile
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

import pytest

from rosace.models import Observation, Target
from rosace.plugins.reduction_plugins.export_demetra import export_demetra as module
from rosace.plugins.reduction_plugins.export_demetra.export_demetra import (
    _TEMPLATE_PATH,
    ExportDemetraPlugin,
    _dec_deg_to_dms,
    _ra_deg_to_hms,
    _to_dotnet_ticks,
)


def _make_obs(
    obs_dir=None,
    star_files=None,
    bias_files=None,
    dark_files=None,
    flat_files=None,
    calib_files=None,
    target=None,
    queried_name=None,
    obs_id="obs12345",
    session_id="sess1234",
) -> Observation:
    obs = Observation(
        id=obs_id, created_at=datetime.now(timezone.utc), session_id=session_id,
        obs_dir=str(obs_dir) if obs_dir else None,
    )
    if star_files is not None:
        obs.sequencer_data["star_spectra_images"] = star_files
    if bias_files is not None:
        obs.sequencer_data["bias_images"] = bias_files
    if dark_files is not None:
        obs.sequencer_data["dark_images"] = dark_files
    if flat_files is not None:
        obs.sequencer_data["flat_images"] = flat_files
    if calib_files is not None:
        obs.sequencer_data["calib_images"] = calib_files
    if target is not None:
        obs.scheduler_data["target"] = target
    if queried_name is not None:
        obs.scheduler_data["queried_name"] = queried_name
    return obs


_FULL_FILES = dict(
    star_files=["star_1.fits"],
    bias_files=["bias_1.fits"],
    dark_files=["dark_1.fits"],
    flat_files=["flat_1.fits"],
    calib_files=["calib_1.fits"],
)


def _obs_missing(key: str) -> Observation:
    """Every required casier present except `key` (its key is absent entirely)."""
    kwargs = dict(_FULL_FILES)
    kwargs[key] = None
    return _make_obs(**kwargs)


def _obs_empty(key: str) -> Observation:
    """Every required casier present but `key` is an empty list."""
    kwargs = dict(_FULL_FILES)
    kwargs[key] = []
    return _make_obs(**kwargs)


class TestExportDemetraPlugin:

    async def test_raises_if_star_spectra_images_missing(self):
        with pytest.raises(RuntimeError, match="star_spectra_images"):
            await ExportDemetraPlugin().execute(_obs_missing("star_files"))

    async def test_raises_if_star_spectra_images_empty(self):
        with pytest.raises(RuntimeError, match="star_spectra_images"):
            await ExportDemetraPlugin().execute(_obs_empty("star_files"))

    async def test_raises_if_bias_files_missing(self):
        with pytest.raises(RuntimeError, match="bias_images"):
            await ExportDemetraPlugin().execute(_obs_missing("bias_files"))

    async def test_raises_if_bias_files_empty(self):
        with pytest.raises(RuntimeError, match="bias_images"):
            await ExportDemetraPlugin().execute(_obs_empty("bias_files"))

    async def test_raises_if_dark_files_missing(self):
        with pytest.raises(RuntimeError, match="dark_images"):
            await ExportDemetraPlugin().execute(_obs_missing("dark_files"))

    async def test_raises_if_dark_files_empty(self):
        with pytest.raises(RuntimeError, match="dark_images"):
            await ExportDemetraPlugin().execute(_obs_empty("dark_files"))

    async def test_raises_if_flat_files_missing(self):
        with pytest.raises(RuntimeError, match="flat_images"):
            await ExportDemetraPlugin().execute(_obs_missing("flat_files"))

    async def test_raises_if_flat_files_empty(self):
        with pytest.raises(RuntimeError, match="flat_images"):
            await ExportDemetraPlugin().execute(_obs_empty("flat_files"))

    async def test_raises_if_calib_files_missing(self):
        with pytest.raises(RuntimeError, match="calib_images"):
            await ExportDemetraPlugin().execute(_obs_missing("calib_files"))

    async def test_raises_if_calib_files_empty(self):
        with pytest.raises(RuntimeError, match="calib_images"):
            await ExportDemetraPlugin().execute(_obs_empty("calib_files"))

    async def test_raises_if_obs_dir_missing(self):
        obs = _make_obs(obs_dir=None, **_FULL_FILES)
        with pytest.raises(RuntimeError, match="obs_dir"):
            await ExportDemetraPlugin().execute(obs)

    async def test_happy_path_produces_valid_srl_and_zip(self, tmp_path):
        all_files = {
            "star_files": ["star_1.fits", "star_2.fits"],
            "bias_files": ["bias_1.fits"],
            "dark_files": ["dark_1.fits"],
            "flat_files": ["flat_1.fits"],
            "calib_files": ["calib_1.fits"],
        }
        for files in all_files.values():
            for name in files:
                (tmp_path / name).touch()

        target = Target(name="HD 12345", ra=157.5, dec=-12.5, magnitude_v=6.2)
        obs = _make_obs(obs_dir=tmp_path, target=target, queried_name="HD 12345", **all_files)

        result = await ExportDemetraPlugin().execute(obs)

        casier = result.reduction_data["export_demetra"]
        srl_path = tmp_path / casier["srl_file"]
        zip_path = tmp_path / casier["zip_file"]
        assert srl_path.is_file()
        assert zip_path.is_file()

        root = ET.parse(srl_path).getroot()
        assert root.attrib["Name"] == "HD 12345"
        assert root.attrib["Version"] == "4"
        obj = root.find("Object")
        assert obj.attrib["IsNameDefined"] == "1"
        assert obj.attrib["Magnitude"] == "6.2"
        coords = obj.find("EquatorialCoordinates")
        assert coords.attrib["RA"] == "10H30M0.0S"
        assert coords.attrib["DEC"] == "-12°30M0.0S"

        baskets = root.findall("Baskets/Basket")
        assert len(baskets) == 5
        bias_basket = next(b for b in baskets if b.find("BasketHeader").attrib["Name"] == "Bias")
        basket_id = bias_basket.find("BasketHeader/BasketId")
        assert basket_id.attrib["SrlType"] == "Offset"
        assert basket_id.attrib["HasNumberId"] == "1"
        assert [img.attrib["FileName"] for img in bias_basket.findall("ImageList/Image")] == all_files["bias_files"]

        process = root.find("Process")
        assert process is not None
        assert process.find(".//area_spectrum").attrib["Top"] == "468"

        assert casier["zip_file"] == f"{tmp_path.name}_sess1234_demetra.zip"
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        expected_raw = [n for files in all_files.values() for n in files]
        assert names[0] == casier["srl_file"]
        assert set(names) == {casier["srl_file"], *expected_raw}
        assert all("/" not in n for n in names)

    async def test_session_observations_share_one_zip_with_calibrations_stored_once(self, tmp_path):
        calibrations = {"bias_files": ["bias_1.fits"], "dark_files": ["dark_1.fits"],
                        "flat_files": ["flat_1.fits"], "calib_files": ["calib_1.fits"]}
        for name in ["vega_1.fits", "vega_2.fits", "deneb_1.fits", *sum(calibrations.values(), [])]:
            (tmp_path / name).touch()
        first = _make_obs(obs_dir=tmp_path, obs_id="obs1", queried_name="Vega",
                          star_files=["vega_1.fits", "vega_2.fits"], **calibrations)
        second = _make_obs(obs_dir=tmp_path, obs_id="obs2", queried_name="Deneb",
                           star_files=["deneb_1.fits"], **calibrations)

        await ExportDemetraPlugin().execute(first)
        await ExportDemetraPlugin().execute(second)

        first_casier = first.reduction_data["export_demetra"]
        second_casier = second.reduction_data["export_demetra"]
        assert first_casier["zip_file"] == second_casier["zip_file"]
        assert len(list(tmp_path.glob("*_demetra.zip"))) == 1
        with zipfile.ZipFile(tmp_path / first_casier["zip_file"]) as zf:
            names = zf.namelist()
            assert zf.testzip() is None
        assert len(names) == len(set(names))  # no member stored twice
        assert set(names) == {
            first_casier["srl_file"], second_casier["srl_file"],
            "vega_1.fits", "vega_2.fits", "deneb_1.fits", *sum(calibrations.values(), []),
        }

    async def test_each_session_gets_its_own_zip(self, tmp_path):
        for name in _FULL_FILES.values():
            (tmp_path / name[0]).touch()
        first = _make_obs(obs_dir=tmp_path, obs_id="obs1", session_id="sessA", queried_name="Vega", **_FULL_FILES)
        second = _make_obs(obs_dir=tmp_path, obs_id="obs2", session_id="sessB", queried_name="Deneb", **_FULL_FILES)

        await ExportDemetraPlugin().execute(first)
        await ExportDemetraPlugin().execute(second)

        zips = sorted(p.name for p in tmp_path.glob("*_demetra.zip"))
        assert zips == [f"{tmp_path.name}_sessA_demetra.zip", f"{tmp_path.name}_sessB_demetra.zip"]
        with zipfile.ZipFile(tmp_path / zips[1]) as zf:
            assert "bias_1.fits" in zf.namelist()  # each session's ZIP is complete on its own

    async def test_re_exported_observation_adds_no_duplicate(self, tmp_path, caplog):
        for name in _FULL_FILES.values():
            (tmp_path / name[0]).touch()
        obs = _make_obs(obs_dir=tmp_path, queried_name="Vega", **_FULL_FILES)

        await ExportDemetraPlugin().execute(obs)
        with caplog.at_level("WARNING", logger="spectro.subplugin.export_demetra"):
            await ExportDemetraPlugin().execute(obs)

        with zipfile.ZipFile(tmp_path / obs.reduction_data["export_demetra"]["zip_file"]) as zf:
            names = zf.namelist()
            assert zf.testzip() is None
        assert len(names) == len(set(names)) == 6  # .obs.srl + 5 FITS
        assert "already in" in caplog.text

    async def test_process_block_matches_template(self, tmp_path):
        files = {k: [f"{k}.fits"] for k in ("star_files", "bias_files", "dark_files", "flat_files", "calib_files")}
        for names in files.values():
            for name in names:
                (tmp_path / name).touch()
        obs = _make_obs(obs_dir=tmp_path, target=Target(name="Vega", ra=0.0, dec=0.0), **files)

        result = await ExportDemetraPlugin().execute(obs)
        srl_path = tmp_path / result.reduction_data["export_demetra"]["srl_file"]

        generated_process = ET.parse(srl_path).getroot().find("Process")
        template_root = ET.fromstring(f"<root>{_TEMPLATE_PATH.read_text(encoding='utf-8')}</root>")
        template_process = template_root.find("Process")

        assert ET.tostring(generated_process, encoding="unicode") == ET.tostring(template_process, encoding="unicode")

    async def test_xml_escaping_of_special_characters(self, tmp_path):
        files = {k: [f"{k}.fits"] for k in ("star_files", "bias_files", "dark_files", "flat_files", "calib_files")}
        for names in files.values():
            for name in names:
                (tmp_path / name).touch()
        tricky_name = 'HD & Co <test> "quoted"'
        obs = _make_obs(obs_dir=tmp_path, queried_name=tricky_name, **files)

        result = await ExportDemetraPlugin().execute(obs)
        srl_path = tmp_path / result.reduction_data["export_demetra"]["srl_file"]

        root = ET.parse(srl_path).getroot()
        assert root.attrib["Name"] == tricky_name


def test_to_dotnet_ticks_matches_known_toto_value():
    # TOTO.obs.srl's real CreationDate="639255310073566314" was produced by
    # Demetra at ~2026-09-20 19:56:47.35663 UTC. .NET ticks have 100ns
    # resolution; Python's datetime only has 1us (10 ticks) resolution, so
    # an exact match isn't achievable -- within one microsecond is the best
    # possible confirmation the formula/constant are correct.
    dt = datetime(2026, 9, 20, 19, 56, 47, 356631, tzinfo=timezone.utc)
    assert abs(_to_dotnet_ticks(dt) - 639255310073566314) < 10


def test_ra_deg_to_hms_zero():
    assert _ra_deg_to_hms(0.0) == "0H0M0.0S"


def test_dec_deg_to_dms_zero():
    assert _dec_deg_to_dms(0.0) == "0°0M0.0S"


def test_ra_deg_to_hms_known_value():
    assert _ra_deg_to_hms(157.5) == "10H30M0.0S"


def test_dec_deg_to_dms_negative():
    assert _dec_deg_to_dms(-12.5) == "-12°30M0.0S"


def test_ra_deg_to_hms_seconds_rounding_carry():
    # Engineered so the raw seconds component is ~59.96 -- must carry into
    # minutes rather than ever rendering the invalid "60.0S".
    ra_deg = 15 * (10 + 30 / 60 + 59.96 / 3600)
    result = _ra_deg_to_hms(ra_deg)
    assert "60.0S" not in result
    assert result == "10H31M0.0S"


def _touch_full_files(directory):
    for files in _FULL_FILES.values():
        for name in files:
            (directory / name).touch()


class TestExportOptions:

    @pytest.mark.parametrize("individual, session, expected", [
        (False, True, {"zip_file"}),
        (True, False, {"individual_zip_file"}),
        (True, True, {"zip_file", "individual_zip_file"}),
    ])
    async def test_individual_and_global_zips(self, tmp_path, monkeypatch, individual, session, expected):
        monkeypatch.setattr(module, "SUBPLUGIN_PARAMS", {"export_demetra": {"individual": individual, "global": session}})
        _touch_full_files(tmp_path)
        obs = _make_obs(obs_dir=tmp_path, queried_name="Vega", **_FULL_FILES)

        result = await ExportDemetraPlugin().execute(obs)

        casier = result.reduction_data["export_demetra"]
        assert set(casier) - {"srl_file"} == expected
        if individual:
            assert casier["individual_zip_file"] == casier["srl_file"].replace(".obs.srl", "_demetra.zip")
            with zipfile.ZipFile(tmp_path / casier["individual_zip_file"]) as zf:
                assert set(zf.namelist()) == {casier["srl_file"], *[n for f in _FULL_FILES.values() for n in f]}
        assert {p.name for p in tmp_path.glob("*.zip")} == {casier[k] for k in expected}

    async def test_nothing_exported_when_both_options_are_false(self, tmp_path, monkeypatch):
        monkeypatch.setattr(module, "SUBPLUGIN_PARAMS", {"export_demetra": {"individual": False, "global": False}})
        _touch_full_files(tmp_path)
        obs = _make_obs(obs_dir=tmp_path, queried_name="Vega", **_FULL_FILES)

        result = await ExportDemetraPlugin().execute(obs)

        assert "export_demetra" not in result.reduction_data
        assert not list(tmp_path.glob("*.srl")) and not list(tmp_path.glob("*.zip"))

    async def test_names_come_from_the_observatory_block(self, tmp_path, monkeypatch):
        monkeypatch.setattr(module, "OBSERVATORY", {
            "observer": "F. Cochard", "site": "St-Pancrasse", "equipment": "Alpy600-72ED-AM3",
            "telescope": "72ED refractor", "spectrograph": "Alpy 600", "camera": "Atik 314L",
        })
        _touch_full_files(tmp_path)
        obs = _make_obs(obs_dir=tmp_path, queried_name="Vega", **_FULL_FILES)

        result = await ExportDemetraPlugin().execute(obs)

        context = ET.parse(tmp_path / result.reduction_data["export_demetra"]["srl_file"]).getroot().find("Context")
        assert context.find("Observer").attrib["Name"] == "F. Cochard"
        assert context.find("Site").attrib["Name"] == "St-Pancrasse"
        assert context.find("Instrument").attrib["Name"] == "Alpy600-72ED-AM3"
        assert context.find("Instrument/Telescope").attrib["Name"] == "72ED refractor"
        assert context.find("Instrument/Spectroscope").attrib["Name"] == "Alpy 600"
        assert context.find("Instrument/Camera").attrib["Name"] == "Atik 314L"

