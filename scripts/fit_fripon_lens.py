"""
Fits the lens model of a FRIPON all-sky camera on the stars of one of its
images, for fripon_image (subplugin_params.fripon.lens).

Run by hand, once per camera (again if it is moved), on an image of a clear
night -- FRIPON keeps only the last 24 hours, so the morning after:

    python scripts/fit_fripon_lens.py --station FRRA12 [--at "2026-10-19 02:00"]
    python scripts/fit_fripon_lens.py --image FILE.jpg --time "2026-10-18T01:09:19" \\
        --latitude 45.62 --longitude -1.03

Without --image, downloads the capture of --station nearest --at (UT; the
latest one by default). The position defaults to the observatory's
(configuration). Searches every orientation (a few seconds), then writes an
annotated PNG (--output) to check by eye, and prints the "lens:" block to
copy under subplugin_params.fripon in the configuration.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml
from matplotlib.image import imread

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "rosace"))

from rosace.config import SITE_LATITUDE_DEG, SITE_LONGITUDE_DEG  # noqa: E402
from rosace.plugins.reduction_plugins.fripon_image.fisheye import fit  # noqa: E402
from rosace.plugins.reduction_plugins.fripon_image.fripon_image import (  # noqa: E402
    download_capture, draw_annotated, fetch_captures, nearest_capture,
)


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace(" ", "T")).replace(tzinfo=timezone.utc)


async def _download(station: str, at: datetime | None) -> tuple[bytes, datetime, str]:
    async with httpx.AsyncClient(timeout=60.0) as client:
        captures = await fetch_captures(client, station)
        capture = nearest_capture(captures, at) if at else (captures[0] if captures else None)
        if capture is None:
            raise SystemExit(f"No capture of {station} in FRIPON's list of the last 24 hours")
        return await download_capture(client, capture), capture.time, capture.file_name


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--station", default="FRRA12")
    parser.add_argument("--at", help="UT time of the capture to use (nearest one), default: the latest")
    parser.add_argument("--image", type=Path, help="a FRIPON JPEG already on disk, instead of downloading one")
    parser.add_argument("--time", help="UT time of --image (required with it)")
    parser.add_argument("--latitude", type=float, default=SITE_LATITUDE_DEG)
    parser.add_argument("--longitude", type=float, default=SITE_LONGITUDE_DEG)
    parser.add_argument("--output", type=Path, default=Path("fripon_lens_check.png"))
    args = parser.parse_args()

    if args.image:
        if not args.time:
            parser.error("--time is required with --image")
        content, when, name = args.image.read_bytes(), _utc(args.time), args.image.name
    else:
        content, when, name = asyncio.run(_download(args.station, _utc(args.at) if args.at else None))
    print(f"Image {name}, {when:%Y-%m-%d %H:%M:%S} UT, site {args.latitude:.4f} {args.longitude:.4f}")

    image = imread(io.BytesIO(content), format="jpeg")
    result = fit(image, when, args.latitude, args.longitude)
    if result is None:
        raise SystemExit("Too few stars found (clouds, daylight?): no lens model. Try a clear-night image.")
    draw_annotated(args.output, image, f"{name} -- {result.matched_count} stars, rms {result.rms_px:.2f} px",
                   result.model, result.positions, result.matched, None)
    print(f"{result.matched_count} stars matched, rms {result.rms_px:.2f} px; check {args.output}")
    print("\nTo copy under subplugin_params.fripon in the configuration:\n")
    print(yaml.safe_dump({"lens": result.model.to_config()}, sort_keys=False))


if __name__ == "__main__":
    main()
