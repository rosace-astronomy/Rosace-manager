"""
REDUCTION sub-plugin — Export to Demetra
Exports the observation as a Demetra-compatible .obs.srl project file plus a
ZIP bundle of every FITS file it references, for reduction with Shelyak's
Demetra software.
"""

from __future__ import annotations

DISPLAY_NAME = "Export Demetra"

import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET

from ....config import SITE_ELEVATION_M, SITE_LATITUDE_DEG, SITE_LONGITUDE_DEG, SITE_NAME, SUBPLUGIN_PARAMS
from ....models import Observation
from ...sub_plugin_base import SubPlugin

log = logging.getLogger("spectro.subplugin.export_demetra")

# Runs in PARALLEL with bias/dark/flat/extract, not instead of them: reads
# straight from sequencer_data, never reduction_data, so it never depends on
# the native pipeline having run -- Rosace's own reduction and a Demetra
# export are two independent, coexisting outputs of the same raw data.
#
# Most of the generated XML (everything under <Process>) is NOT computed
# here: it's a static, per-instrument template extracted byte-for-byte from
# a real, freshly-created (zero images, zero processing) Demetra project for
# this exact instrument -- Demetra itself fills these values in progressively
# as it reduces, so Rosace must never compute or guess them. See
# demetra_process_template.xml, next to this file.
#
# Two fields are UNVERIFIED against real Demetra behavior (no non-zero
# declination example was available while writing this): the sign format in
# _dec_deg_to_dms(), and whether Object/@IsNameDefined="1" behaves as
# expected. Check both on the first real export opened in Demetra.

_TEMPLATE_PATH = Path(__file__).parent / "demetra_process_template.xml"

# .NET ticks: 100ns intervals since 0001-01-01T00:00:00, Demetra's own
# CreationDate convention. Formula and constant verified against a real
# Demetra file's own CreationDate -- see test_export_demetra.py.
_TICKS_PER_SECOND = 10_000_000
_SECONDS_YEAR1_TO_UNIX_EPOCH = 62_135_596_800
_UNIX_EPOCH_UTC = datetime(1970, 1, 1, tzinfo=timezone.utc)

_BASKET_NAMES = ("Object", "Bias", "Dark", "Flat", "Calibration")
_BASKET_SRL_TYPES = ("Object", "Offset", "Dark", "Flat", "Calibration")


def _to_dotnet_ticks(dt_utc: datetime) -> int:
    """Converts a timezone-aware UTC datetime to .NET ticks. Integer
    arithmetic throughout, deliberately not dt_utc.timestamp() -- at this
    magnitude (~6.4e17) a float64 loses more precision than the 100ns this
    is meant to represent, silently drifting the result by tens of ticks."""
    delta = dt_utc - _UNIX_EPOCH_UTC
    unix_ticks = delta.days * 86400 * _TICKS_PER_SECOND + delta.seconds * _TICKS_PER_SECOND + delta.microseconds * 10
    return _SECONDS_YEAR1_TO_UNIX_EPOCH * _TICKS_PER_SECOND + unix_ticks


def _carry_rounding(major: int, minor: int, seconds: float, minor_max: int, major_max: int) -> tuple[int, int, float]:
    """Rounds seconds to 1 decimal, carrying into minor/major on overflow --
    e.g. 59.96s must become 0.0s + 1 minute, never a nonsensical "60.0s"."""
    seconds = round(seconds, 1)
    if seconds >= 60.0:
        seconds = 0.0
        minor += 1
    if minor >= minor_max:
        minor = 0
        major = (major + 1) % major_max
    return major, minor, seconds


def _ra_deg_to_hms(ra_deg: float) -> str:
    """Converts RA in decimal degrees [0, 360) to Demetra's "HhMmS.sS"
    sexagesimal string. RA is always non-negative -- no sign to handle."""
    total_hours = (ra_deg % 360) / 15.0
    h = int(total_hours)
    total_minutes = (total_hours - h) * 60
    m = int(total_minutes)
    s = (total_minutes - m) * 60
    h, m, s = _carry_rounding(h, m, s, minor_max=60, major_max=24)
    return f"{h}H{m}M{s:.1f}S"


def _dec_deg_to_dms(dec_deg: float) -> str:
    """Converts DEC in decimal degrees [-90, 90] to Demetra's "D°MmS.sS"
    sexagesimal string, sign prefixed on the degrees field. UNVERIFIED
    against real Demetra -- no non-zero-declination example was available
    while writing this; confirm on the first real export."""
    sign = "-" if dec_deg < 0 else ""
    dec_abs = abs(dec_deg)
    d = int(dec_abs)
    total_minutes = (dec_abs - d) * 60
    m = int(total_minutes)
    s = (total_minutes - m) * 60
    d, m, s = _carry_rounding(d, m, s, minor_max=60, major_max=91)  # 91: never actually wraps, dec_abs <= 90
    return f"{sign}{d}°{m}M{s:.1f}S"


def _export_base_name(obs: Observation) -> str:
    """Mirrors Observation.suggested_filename's own <iso_datetime>_<name>
    logic (without the .yaml extension), so the .obs.srl/.zip sit next to
    the observation's own YAML with a recognizably related name."""
    target = obs.scheduler_data.get("target")
    name = obs.scheduler_data.get("queried_name") or (target.name if target else None) or "target"
    iso_str = obs.created_at.strftime("%Y%m%dT%H%M%S")
    safe_name = name.replace(" ", "_").replace("/", "-")
    return f"{iso_str}_{safe_name}"


class ExportDemetraPlugin(SubPlugin):
    """Exports the observation as a Demetra-compatible .obs.srl + ZIP."""

    async def execute(self, obs: Observation) -> Observation:
        if "star_spectra_images" not in obs.sequencer_data:
            raise RuntimeError(
                "EXPORT_DEMETRA: sequencer_data['star_spectra_images'] missing — "
                "no acquisition sub-plugin ran before EXPORT_DEMETRA"
            )
        star_files: list[str] = obs.sequencer_data["star_spectra_images"]
        if not star_files:
            raise RuntimeError("EXPORT_DEMETRA: no raw file (sequencer_data['star_spectra_images'] empty)")

        if "bias_acquisition" not in obs.sequencer_data or "bias_files" not in obs.sequencer_data["bias_acquisition"]:
            raise RuntimeError(
                "EXPORT_DEMETRA: sequencer_data['bias_acquisition']['bias_files'] missing — "
                "bias_acquisition did not run before EXPORT_DEMETRA"
            )
        bias_files: list[str] = obs.sequencer_data["bias_acquisition"]["bias_files"]
        if not bias_files:
            raise RuntimeError("EXPORT_DEMETRA: no bias file (sequencer_data['bias_acquisition']['bias_files'] empty)")

        if "dark_acquisition" not in obs.sequencer_data or "dark_files" not in obs.sequencer_data["dark_acquisition"]:
            raise RuntimeError(
                "EXPORT_DEMETRA: sequencer_data['dark_acquisition']['dark_files'] missing — "
                "dark_acquisition did not run before EXPORT_DEMETRA"
            )
        dark_files: list[str] = obs.sequencer_data["dark_acquisition"]["dark_files"]
        if not dark_files:
            raise RuntimeError("EXPORT_DEMETRA: no dark file (sequencer_data['dark_acquisition']['dark_files'] empty)")

        if "flat_acquisition" not in obs.sequencer_data or "flat_files" not in obs.sequencer_data["flat_acquisition"]:
            raise RuntimeError(
                "EXPORT_DEMETRA: sequencer_data['flat_acquisition']['flat_files'] missing — "
                "flat_acquisition did not run before EXPORT_DEMETRA"
            )
        flat_files: list[str] = obs.sequencer_data["flat_acquisition"]["flat_files"]
        if not flat_files:
            raise RuntimeError("EXPORT_DEMETRA: no flat file (sequencer_data['flat_acquisition']['flat_files'] empty)")

        if "calib_acquisition" not in obs.sequencer_data or "calib_files" not in obs.sequencer_data["calib_acquisition"]:
            raise RuntimeError(
                "EXPORT_DEMETRA: sequencer_data['calib_acquisition']['calib_files'] missing — "
                "calib_acquisition did not run before EXPORT_DEMETRA"
            )
        calib_files: list[str] = obs.sequencer_data["calib_acquisition"]["calib_files"]
        if not calib_files:
            raise RuntimeError(
                "EXPORT_DEMETRA: no calibration file (sequencer_data['calib_acquisition']['calib_files'] empty)"
            )

        if not obs.obs_dir:
            raise RuntimeError("EXPORT_DEMETRA: observation directory (obs_dir) not set")

        obs_dir = Path(obs.obs_dir)
        baskets = list(
            zip(_BASKET_NAMES, _BASKET_SRL_TYPES, (star_files, bias_files, dark_files, flat_files, calib_files))
        )

        params = SUBPLUGIN_PARAMS.get("export_demetra", {})
        target = obs.scheduler_data.get("target")
        observation_name = obs.scheduler_data.get("queried_name") or (target.name if target else None) or "target"

        root = ET.Element(
            "Observation",
            {
                "Name": observation_name,
                "CreationDate": str(_to_dotnet_ticks(datetime.now(timezone.utc))),
                "Version": "4",
                "DemetraVersion": "9.0.0.0",
            },
        )

        obj = ET.SubElement(
            root,
            "Object",
            {
                "Name": observation_name,
                "IsNameDefined": "1",
                "Description": "",
                "Type": "PointLike",
                "Magnitude": str(target.magnitude_v if target and target.magnitude_v is not None else 0),
            },
        )
        ET.SubElement(
            obj,
            "EquatorialCoordinates",
            {
                "RA": _ra_deg_to_hms(target.ra) if target else "0H0M0.0S",
                "DEC": _dec_deg_to_dms(target.dec) if target else "0°0M0.0S",
            },
        )

        ctx = ET.SubElement(root, "Context", {"Version": "3.0"})
        ET.SubElement(ctx, "Observer", {"Name": params.get("observer_name", "")})
        ET.SubElement(
            ctx,
            "Site",
            {
                "Name": SITE_NAME,
                "Description": "",
                "Latitude": str(SITE_LATITUDE_DEG),
                "Longitude": str(SITE_LONGITUDE_DEG),
                "Altitude": str(SITE_ELEVATION_M),
                "UAICode": params.get("site_uai_code", ""),
            },
        )
        instrument = ET.SubElement(ctx, "Instrument", {"Version": "1.0", "Name": params.get("instrument_name", "")})
        ET.SubElement(
            instrument,
            "Telescope",
            {
                "Version": "2",
                "Name": params.get("telescope_name", ""),
                "Diameter_mm": str(params.get("telescope_diameter_mm", 0)),
                "Focal_mm": str(params.get("telescope_focal_mm", 0)),
                "Description": "",
            },
        )
        ET.SubElement(instrument, "Spectroscope", {"Name": "", "Description": "", "SlitPresence": "False"})
        ET.SubElement(
            instrument,
            "Camera",
            {
                "Version": "1",
                "Name": params.get("camera_name", ""),
                "Description": "",
                "SensorType": params.get("sensor_type", "CCD"),
            },
        )

        baskets_el = ET.SubElement(root, "Baskets", {"Version": "1"})
        for name, srl_type, files in baskets:
            basket = ET.SubElement(baskets_el, "Basket", {"Version": "1"})
            header = ET.SubElement(
                basket,
                "BasketHeader",
                {
                    "Version": "2",
                    "Name": name,
                    "Duplicated": "False" if name == "Object" else "True",
                    "BasketType": "Generic",
                },
            )
            ET.SubElement(
                header,
                "BasketId",
                {"Version": "3", "SrlType": srl_type, "Num": "1", "HasNumberId": "1" if files else "0"},
            )
            image_list = ET.SubElement(basket, "ImageList")
            for f in files:
                ET.SubElement(image_list, "Image", {"FileName": f})

        xml_body = ET.tostring(root, encoding="unicode")
        process_block = _TEMPLATE_PATH.read_text(encoding="utf-8")
        closing_tag = "</Observation>"
        assert xml_body.endswith(closing_tag)
        document = '<?xml version="1.0"?>\n' + xml_body[: -len(closing_tag)] + process_block + closing_tag

        base_name = _export_base_name(obs)
        srl_filename = f"{base_name}.obs.srl"
        srl_path = obs_dir / srl_filename
        srl_path.write_text(document, encoding="utf-8")
        total_raw = len(star_files) + len(bias_files) + len(dark_files) + len(flat_files) + len(calib_files)
        log.info(f"[EXPORT_DEMETRA] Wrote Demetra project file {srl_filename} — {total_raw} raw file(s) referenced")

        zip_filename = f"{base_name}_demetra.zip"
        zip_path = obs_dir / zip_filename
        with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.write(srl_path, arcname=srl_filename)
            for _, _, files in baskets:
                for f in files:
                    zf.write(obs_dir / f, arcname=f)
        log.info(f"[EXPORT_DEMETRA] Built ZIP {zip_filename} — {1 + total_raw} member(s)")

        obs.reduction_data["export_demetra"] = {"srl_file": srl_filename, "zip_file": zip_filename}
        return obs
