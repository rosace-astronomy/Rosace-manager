"""
Builds rosace/rosace/data/bright_stars.csv, the bright stars Rosace finds on
all-sky images (fripon_image's fisheye model): every star of the whole sky
brighter than V 3.5, from Simbad, with its usual name.

Reference data that never changes, committed with the code -- not targets.db,
the observing catalogue. Run by hand, once, then only to change the
magnitude limit:

    python scripts/build_bright_stars.py [--output FILE] [--vmax 3.5]

The usual name is the shortest one-word name among Simbad's "NAME"
identifiers (Polaris rather than "North Star"); stars without one keep only
their Simbad name.
"""

from __future__ import annotations

import argparse
import csv
import io
from pathlib import Path

import httpx

TAP_URL = "https://simbad.cds.unistra.fr/simbad/sim-tap/sync"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "rosace" / "rosace" / "data" / "bright_stars.csv"
TIMEOUT_SECONDS = 120.0


def _query(adql: str) -> list[dict]:
    response = httpx.post(TAP_URL, timeout=TIMEOUT_SECONDS, data={
        "REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv", "QUERY": adql})
    response.raise_for_status()
    if response.text.lstrip().startswith("<"):
        raise RuntimeError(f"Simbad refused the query: {response.text[:300]}")
    return list(csv.DictReader(io.StringIO(response.text)))


def usual_name(names: list[str]) -> str:
    """The shortest one-word name, else the shortest name ("" when none)."""
    if not names:
        return ""
    one_word = [n for n in names if " " not in n]
    return min(one_word or names, key=lambda n: (len(n), n))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--vmax", type=float, default=3.5)
    args = parser.parse_args()

    stars = _query(
        "SELECT basic.main_id, basic.ra, basic.dec, flux.flux FROM basic "
        "JOIN flux ON flux.oidref = basic.oid "
        f"WHERE flux.filter = 'V' AND flux.flux < {args.vmax} AND basic.ra IS NOT NULL")
    idents = _query(
        "SELECT basic.main_id, ident.id FROM basic "
        "JOIN ident ON ident.oidref = basic.oid JOIN flux ON flux.oidref = basic.oid "
        f"WHERE flux.filter = 'V' AND flux.flux < {args.vmax} AND ident.id LIKE 'NAME %'")
    names: dict[str, list[str]] = {}
    for row in idents:
        names.setdefault(row["main_id"], []).append(row["id"].removeprefix("NAME ").strip())

    # Stars only: Simbad also gives a V magnitude to galaxies and clusters
    # (LMC, M 31, NGC 1980...), whose main names never start like a star's
    stars = [s for s in stars if s["main_id"].startswith(("* ", "V* "))]
    rows = sorted(({"simbad_name": " ".join(s["main_id"].split()),
                    "usual_name": usual_name(names.get(s["main_id"], [])),
                    "ra_deg": f"{float(s['ra']):.6f}", "dec_deg": f"{float(s['dec']):.6f}",
                    "v": f"{float(s['flux']):.2f}"} for s in stars),
                  key=lambda r: float(r["v"]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="") as f:
        f.write(f"# Stars brighter than V {args.vmax}, whole sky, from Simbad "
                "(scripts/build_bright_stars.py); ra/dec J2000 degrees\n")
        writer = csv.DictWriter(f, fieldnames=["simbad_name", "usual_name", "ra_deg", "dec_deg", "v"])
        writer.writeheader()
        writer.writerows(rows)
    named = sum(1 for r in rows if r["usual_name"])
    print(f"{len(rows)} stars ({named} with a usual name) -> {args.output}")


if __name__ == "__main__":
    main()
