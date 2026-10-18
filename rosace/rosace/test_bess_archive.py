"""
Tests of bess_archive.py: the BeSS SSAP answers, the star lookup order, the
spectra read (both FITS forms), the degraded H-alpha view and the set of
earlier spectra shown on the BeSS validation page. BeSS itself is never
contacted: its queries and downloads are replaced by fakes.
"""

import io
import uuid
from datetime import datetime, timezone

import numpy as np
import pytest
import pytest_asyncio
from astropy.io import fits

from rosace import bess_archive
from rosace.bess_archive import (
    BessArchiveError, bess_names_by_hd, degrade, group_observations, halpha_equivalent_width, parse_votable,
    previous_spectra, read_spectrum,
)
from rosace.database import db_get_bess_validation, db_init, db_insert_session, db_queue_bess_validation
from rosace.models import SessionContext
from rosace.targets import StarCreate, star_add_alias, star_insert, targets_db_init

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
SITE = "St-Pancrasse"


@pytest_asyncio.fixture(autouse=True)
async def init_db():
    await db_init()
    await targets_db_init()


# ─────────────────────────────────────────────────────────────
# Fakes
# ─────────────────────────────────────────────────────────────

def _votable(rows: list[dict], status: str = "OK") -> str:
    """An SSAP answer with BeSS's fields (only those Rosace reads)."""
    fields = ["acref", "creatorDID", "targetName", "boundsSpectralStart", "boundsSpectralStop", "boundsTimeStart"]
    trs = "".join("<TR>" + "".join(f"<TD>{row.get(f, '')}</TD>" for f in fields) + "</TR>" for row in rows)
    return (f'<?xml version="1.0"?><VOTABLE version="1"><RESOURCE type="results">'
            f'<INFO name="QUERY_STATUS" value="{status}"/><TABLE>'
            + "".join(f'<FIELD ID="{f}" datatype="char"/>' for f in fields)
            + f"<DATA><TABLEDATA>{trs}</TABLEDATA></DATA></TABLE></RESOURCE></VOTABLE>")


def _row(spectrum_id, start, directory="A_obs/instr", lo=6500, hi=6650, target="X"):
    return {"acref": f"http://basebe.obspm.fr/cgi-bin/extBeSS.pl?fits={directory.replace('/', '%2F')}%2Ff{spectrum_id}.fits",
            "creatorDID": f"BeSS:{spectrum_id}", "targetName": target,
            "boundsSpectralStart": f"{lo}e-10", "boundsSpectralStop": f"{hi}e-10", "boundsTimeStart": start}


def _halpha(wavelength, peak=2.0, sigma=1.0):
    return 1 + (peak - 1) * np.exp(-0.5 * ((wavelength - 6562.8) / sigma) ** 2)


def _image_fits(start, *, crval=6500.0, cdelt=0.1, n=2000, peak=2.0, sigma=1.0, **cards) -> bytes:
    wavelength = crval + np.arange(n) * cdelt
    hdu = fits.PrimaryHDU(_halpha(wavelength, peak, sigma).astype(np.float32))
    hdu.header.update({"CRVAL1": crval, "CDELT1": cdelt, "CRPIX1": 1, "DATE-OBS": start, **cards})
    buffer = io.BytesIO()
    hdu.writeto(buffer)
    return buffer.getvalue()


def _table_fits(start, **cards) -> bytes:
    wavelength = np.linspace(6480, 6700, 3000)
    table = fits.BinTableHDU.from_columns([
        fits.Column("WAVE", "E", array=wavelength),
        fits.Column("FLUX", "E", array=_halpha(wavelength, 2.4, 0.8)),
        fits.Column("ERROR", "E", array=np.zeros_like(wavelength)),
    ])
    primary = fits.PrimaryHDU()
    primary.header.update({"DATE-OBS": start, **cards})
    buffer = io.BytesIO()
    fits.HDUList([primary, table]).writeto(buffer)
    return buffer.getvalue()


class FakeBess:
    """Answers SSAP queries from {TARGETNAME or POS: rows}; records them."""

    def __init__(self, by_target: dict, files: dict | None = None, overflow_until_days: int | None = None):
        self.by_target = by_target
        self.files = files or {}
        self.overflow_until_days = overflow_until_days
        self.queries: list[dict] = []
        self.downloads: list[str] = []

    async def query(self, client, params):
        self.queries.append(params)
        target = params.get("TARGETNAME") or params.get("POS")
        start = datetime.fromisoformat(params["TIME"].split("/")[0]).replace(tzinfo=timezone.utc)
        if self.overflow_until_days is not None and (NOW - start).days > self.overflow_until_days:
            return _votable([], "OVERFLOW")
        return _votable(self.by_target.get(target, []))

    async def download(self, client, url):
        self.downloads.append(url)
        return self.files[url.rsplit("%2Ff", 1)[1].removesuffix(".fits")]


@pytest.fixture
def installation(tmp_path, monkeypatch):
    robot = tmp_path / "robot.txt"
    robot.write_text("File created\n"
                     "Name           HD      RA            DEC           Magn    Last_Obs              Prior.  Obs_Period  \n"
                     "V695 Mon       65875   +08 00 44.1   -02 52 54.9   6.51    2026-02-25 20:47:43   3       365         \n"
                     "BD+62 271      0       +02 00 00.0   +62 00 00.0   9.50    0000-00-00 00:00:00   3       365         \n",
                     encoding="utf-8")
    monkeypatch.setattr(bess_archive, "SUBPLUGIN_PARAMS", {"bess_archive": {"robot_path": str(robot)}})
    monkeypatch.setattr(bess_archive, "BESS_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(bess_archive, "OBSERVATORY", {"site": SITE})
    return tmp_path


def _use(monkeypatch, fake: FakeBess):
    monkeypatch.setattr(bess_archive, "_ssap_query", fake.query)
    monkeypatch.setattr(bess_archive, "_download", fake.download)


async def _star(name, *aliases, ra=120.18, dec=-2.88):
    star_id = await star_insert(StarCreate(name=name, ra=ra, dec=dec))
    for alias in aliases:
        await star_add_alias(star_id, alias)
    return star_id


async def _rosace_spectrum(directory, star, start, status="pending", **cards) -> str:
    """A Rosace spectrum in the validation list, its final FITS in its session directory."""
    obs_id = uuid.uuid4().hex[:8]
    session = SessionContext(site_name=SITE, session_dir=str(directory))
    await db_insert_session(session)
    name = f"{obs_id}.fits"
    (directory / name).write_bytes(_image_fits(start, crval=3800, cdelt=3.47, n=1080, peak=1.6, sigma=4.6,
                                               BSS_ITRP=600, BSS_SITE=SITE, OBSERVER="Rosace", **cards))
    await db_queue_bess_validation(obs_id, session.session_id, star, name)
    if status != "pending":
        import aiosqlite
        from rosace.config import DB_PATH
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("UPDATE bess_validation SET status = ? WHERE observation_id = ?", (status, obs_id))
            await db.commit()
    return obs_id


# ─────────────────────────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────────────────────────

def test_parse_votable_rows_and_overflow():
    rows = parse_votable(_votable([_row("1", "2026-03-14 11:33:26")]))
    assert rows[0]["creatorDID"] == "BeSS:1" and rows[0]["boundsTimeStart"] == "2026-03-14 11:33:26"
    assert parse_votable(_votable([], "OVERFLOW")) is None
    with pytest.raises(BessArchiveError, match="ERROR"):
        parse_votable(_votable([], "ERROR"))
    with pytest.raises(BessArchiveError, match="unreadable"):
        parse_votable("not xml")


def test_robot_names_by_hd():
    text = ("File created\nName           HD      RA            DEC           Magn    Last_Obs              Prior.  Obs_Period  \n"
            "V695 Mon       65875   +08 00 44.1   -02 52 54.9   6.51    2026-02-25 20:47:43   3       365         \n"
            "BD+62 271      0       +02 00 00.0   +62 00 00.0   9.50    0000-00-00 00:00:00   3       365         \n")
    assert bess_names_by_hd(text) == {65875: "V695 Mon"}


def test_group_observations_keeps_one_halpha_order_per_spectrum():
    rows = [
        _row("10", "2026-02-23 14:51:32", "A_pl/eshel", 4200, 4300),     # another order: no H-alpha
        _row("11", "2026-02-23 14:51:32", "A_pl/eshel", 6480, 6640),
        _row("12", "2026-02-23 14:51:32", "A_pl/eshel", 6550, 6700),     # same spectrum, overlapping order
        _row("20", "2026-02-25 20:42:57", "A_eb/lhires", 6489, 6644),
        _row("21", "2026-02-25 20:43:10", "A_gs/lhires", 6475, 6625),    # same time, another observer
    ]
    observations = group_observations(rows)
    assert [o.spectrum_id for o in observations] == ["21", "20", "11"]
    assert observations[2].directory == "A_pl/eshel"


def test_read_spectrum_image_and_table(tmp_path):
    image = tmp_path / "image.fits"
    image.write_bytes(_image_fits("2026-01-01T00:00:00", CUNIT1="nm", crval=650.0, cdelt=0.01))
    wavelength, flux, header = read_spectrum(image)
    assert wavelength[0] == pytest.approx(6500.0) and wavelength[1] - wavelength[0] == pytest.approx(0.1)
    assert flux.size == 2000 and header["DATE-OBS"] == "2026-01-01T00:00:00"

    table = tmp_path / "table.fits"
    table.write_bytes(_table_fits("2026-02-02T00:00:00", OBSERVER="Paul"))
    wavelength, flux, header = read_spectrum(table)
    assert wavelength.size == 3000 and flux.max() == pytest.approx(2.4, abs=0.01)
    assert header["OBSERVER"] == "Paul"


def test_degrade_lowers_the_peak_and_keeps_the_flux():
    wavelength = np.arange(4000, 6750, 0.1)
    flux = _halpha(wavelength, 2.5, 0.8)
    grid, degraded = degrade(wavelength, flux, 13000, 600)
    assert grid[0] == pytest.approx(4000) and grid[-1] == pytest.approx(6750, abs=2)   # the whole spectrum
    ratios = grid[1:] / grid[:-1]
    assert ratios == pytest.approx(np.full(ratios.size, ratios[0]))                       # regular in ln(wavelength)
    assert degraded.max() < 1.3
    excess = lambda w, f: np.trapezoid(f - 1, w)
    assert excess(grid, degraded) == pytest.approx(excess(wavelength, flux), rel=0.02)
    assert degrade(wavelength, flux, 700, 600) is None                    # not finer than the target
    assert degrade(np.arange(6400, 6750, 3.5), np.ones(100), None, 600) is None   # R unknown, coarse


def test_scale_to_unit_same_rule_for_all():
    wavelength = np.arange(6490, 6630, 0.5)
    flux = 3.0 * _halpha(wavelength, 2.0, 2.0)
    scaled = bess_archive.scale_to_unit(wavelength, flux)
    continuum = ((wavelength >= 6510) & (wavelength <= 6530)) | ((wavelength >= 6595) & (wavelength <= 6615))
    assert np.median(scaled[continuum]) == pytest.approx(1.0)
    short = wavelength < 6595                                   # red continuum zone not covered
    assert bess_archive.scale_to_unit(wavelength[short], flux[short]) is None


def test_halpha_equivalent_width():
    wavelength = np.arange(6450, 6700, 0.5)
    emission = 1.05 + 0.0001 * (wavelength - 6560) + 2.0 * np.exp(-0.5 * ((wavelength - 6562.8) / 3) ** 2)
    continuum = 1.05 + 0.0001 * (wavelength - 6560)
    expected = -2.0 * 3 * np.sqrt(2 * np.pi) / 1.05            # negative in emission
    assert halpha_equivalent_width(wavelength, emission) == pytest.approx(expected, rel=0.01)
    assert halpha_equivalent_width(wavelength, continuum) == pytest.approx(0, abs=1e-6)
    assert halpha_equivalent_width(wavelength[wavelength < 6595], emission[wavelength < 6595]) is None


# ─────────────────────────────────────────────────────────────
# Star lookup
# ─────────────────────────────────────────────────────────────

async def test_lookup_robot_name_first(installation, monkeypatch):
    fake = FakeBess({"V695 Mon": [_row("1", "2026-02-25 20:47:43", target="V695 Mon")]})
    _use(monkeypatch, fake)
    found_by, observations = await bess_archive.find_bess_observations(None, ["HD  65875", "HD 65875", "V695 Mon"],
                                                                       120.18, -2.88, NOW)
    assert found_by == "robot" and len(observations) == 1
    assert len(fake.queries) == 1 and fake.queries[0]["TARGETNAME"] == "V695 Mon"
    assert fake.queries[0]["BAND"] == bess_archive.HALPHA_BAND


async def test_lookup_by_names_then_position(installation, monkeypatch):
    fake = FakeBess({"BD+62 271": [_row("2", "2026-01-01 00:00:00")]})
    _use(monkeypatch, fake)
    found_by, _ = await bess_archive.find_bess_observations(None, ["BD+62   271", "BD+62 271"], 30.0, 62.0, NOW)
    assert found_by == "name"
    assert [q["TARGETNAME"] for q in fake.queries] == ["BD+62   271", "BD+62 271"]

    fake = FakeBess({"30.0,62.0": [_row("3", "2026-01-01 00:00:00", target="neighbour")]})
    _use(monkeypatch, fake)
    found_by, observations = await bess_archive.find_bess_observations(None, ["Unknown"], 30.0, 62.0, NOW)
    assert found_by == "position" and observations[0].target_name == "neighbour"
    assert fake.queries[-1]["SIZE"] == str(bess_archive.SEARCH_RADIUS_DEG)

    _use(monkeypatch, FakeBess({}))
    assert await bess_archive.find_bess_observations(None, ["Unknown"], 30.0, 62.0, NOW) == (None, [])


async def test_overflow_narrows_the_window(installation, monkeypatch):
    fake = FakeBess({"gam Cas": [_row("4", "2026-09-27 01:48:00")]}, overflow_until_days=400)
    _use(monkeypatch, fake)
    _, observations = await bess_archive.find_bess_observations(None, ["gam Cas"], None, None, NOW)
    assert len(observations) == 1
    assert [q["TIME"].split("/")[0] for q in fake.queries] == ["2000-01-01", "2024-10-18", "2025-10-18"]

    _use(monkeypatch, FakeBess({"gam Cas": []}, overflow_until_days=0))
    with pytest.raises(BessArchiveError, match="too many"):
        await bess_archive.find_bess_observations(None, ["gam Cas"], None, None, NOW)


# ─────────────────────────────────────────────────────────────
# The comparison set
# ─────────────────────────────────────────────────────────────

async def test_previous_spectra_most_recent_five_with_rosace_twin(installation, monkeypatch):
    star = f"HD  {uuid.uuid4().int % 10**6}"
    await _star(star, "V695 Mon")
    current = await _rosace_spectrum(installation, star, "2026-10-17T02:54:32")
    older = await _rosace_spectrum(installation, star, "2026-09-25T03:00:00")
    sent = await _rosace_spectrum(installation, star, "2026-10-10T03:00:00", status="sent")
    await _rosace_spectrum(installation, star, "2026-10-15T03:00:00", status="rejected")
    rows = [
        _row("100", "2026-10-10 03:00:20", "A_rosace/alpy", 3750, 7500),   # the sent Rosace spectrum, in BeSS
        _row("101", "2026-10-01 20:00:00"), _row("102", "2026-08-01 20:00:00"),
        _row("103", "2026-07-01 20:00:00"), _row("104", "2026-06-01 20:00:00"),
    ]
    files = {"100": _image_fits("2026-10-10T03:00:20", crval=3750, cdelt=3.47, n=1080, BSS_SITE=SITE,
                                OBSERVER="F. Cochard", BSS_ITRP=600),
             "101": _table_fits("2026-10-01T20:00:00", OBSERVER="Paul", BSS_ITRP=13000),
             "102": _image_fits("2026-08-01T20:00:00", OBSERVER="Eric"),
             "103": _image_fits("2026-07-01T20:00:00"), "104": _image_fits("2026-06-01T20:00:00")}
    fake = FakeBess({"V695 Mon": rows}, files)
    _use(monkeypatch, fake)

    result = await previous_spectra(await db_get_bess_validation(current), now=NOW)
    assert result["found_by"] == "name" and result["bess_error"] is None
    assert result["current"]["observation_id"] == current
    spectra = result["spectra"]
    assert [(s["source"], s["date_obs"][:10]) for s in spectra] == [
        ("bess_rosace", "2026-10-10"), ("bess", "2026-10-01"), ("rosace", "2026-09-25"),
        ("bess", "2026-08-01"), ("bess", "2026-07-01")]
    assert spectra[0]["source"] == "bess_rosace" and spectra[0]["observation_id"] == sent
    assert spectra[0]["observer"] == "F. Cochard"
    assert any(s["source"] == "rosace" and s["observation_id"] == older for s in spectra)
    assert all(s.get("observation_id") != current for s in spectra)          # never itself
    assert len(spectra) == 5 and len(fake.downloads) == 4                    # downloads only for those kept
    paul = next(s for s in spectra if s["observer"] == "Paul")
    assert paul["degraded"] is not None and paul["resolving_power"] == 13000
    assert spectra[0]["degraded"] is None                                    # same resolution as Rosace
    assert paul["halpha_ew"] < 0 and result["current"]["halpha_ew"] < 0     # emission
    assert all(s["scaled"] for s in spectra) and result["current"]["scaled"]


async def test_previous_spectra_twin_from_another_site_counts_twice(installation, monkeypatch):
    star = f"HD  {uuid.uuid4().int % 10**6}"
    await _star(star)
    current = await _rosace_spectrum(installation, star, "2026-10-17T02:54:32")
    await _rosace_spectrum(installation, star, "2026-10-10T03:00:00")
    files = {"200": _image_fits("2026-10-10T03:00:10", BSS_SITE="Elsewhere", OBSERVER="Other")}
    _use(monkeypatch, FakeBess({star: [_row("200", "2026-10-10 03:00:10")]}, files))
    spectra = (await previous_spectra(await db_get_bess_validation(current), now=NOW))["spectra"]
    assert sorted(s["source"] for s in spectra) == ["bess", "rosace"]


async def test_previous_spectra_bess_failure_keeps_rosace(installation, monkeypatch):
    star = f"HD  {uuid.uuid4().int % 10**6}"
    await _star(star)
    current = await _rosace_spectrum(installation, star, "2026-10-17T02:54:32")
    await _rosace_spectrum(installation, star, "2026-09-25T03:00:00")

    async def unreachable(client, params):
        import httpx
        raise httpx.ConnectError("no network")
    monkeypatch.setattr(bess_archive, "_ssap_query", unreachable)
    result = await previous_spectra(await db_get_bess_validation(current), now=NOW)
    assert "unreachable" in result["bess_error"]
    assert [s["source"] for s in result["spectra"]] == ["rosace"]


async def test_previous_spectra_cache_reused(installation, monkeypatch):
    star = f"HD  {uuid.uuid4().int % 10**6}"
    await _star(star)
    current = await _rosace_spectrum(installation, star, "2026-10-17T02:54:32")
    fake = FakeBess({star: [_row("300", "2026-10-01 20:00:00")]}, {"300": _image_fits("2026-10-01T20:00:00")})
    _use(monkeypatch, fake)
    row = await db_get_bess_validation(current)
    await previous_spectra(row, now=NOW)
    await previous_spectra(row, now=NOW)
    assert len(fake.downloads) == 1
    assert (installation / "cache" / "300.fits").is_file()
