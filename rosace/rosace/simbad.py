# SPDX-FileCopyrightText: 2026 The ROSACE project
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Queries the Simbad database (CDS Strasbourg) via the TAP API.
Returns a star's information as a StarCreate.
"""

import logging
from typing import Optional

import httpx

from .targets import StarCreate

log = logging.getLogger("spectro.simbad")

SIMBAD_TAP_URL = "https://simbad.cds.unistra.fr/simbad/sim-tap/sync"

ADQL_QUERY = """
SELECT
    main_id,
    ra,
    dec,
    sp_type,
    allfluxes.V AS vmag
FROM basic
JOIN ident ON basic.oid = ident.oidref
LEFT JOIN allfluxes ON basic.oid = allfluxes.oidref
WHERE ident.id = '{name}'
"""


class SimbadNotFoundError(Exception):
    """The object was not found in Simbad."""
    pass


class SimbadQueryError(Exception):
    """Error while querying Simbad."""
    pass


async def simbad_search(name: str) -> StarCreate:
    """
    Queries Simbad for a star name and returns a StarCreate.

    :param name: Star name (e.g. "Vega", "Sirius", "HD 187642")
    :raises SimbadNotFoundError: If the star isn't found
    :raises SimbadQueryError: On a communication error
    """
    query = ADQL_QUERY.format(name=name.replace("'", "''"))  # SQL escaping

    params = {
        "REQUEST": "doQuery",
        "LANG":    "ADQL",
        "FORMAT":  "json",
        "QUERY":   query,
    }

    log.info(f"[SIMBAD] Querying: {name}")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(SIMBAD_TAP_URL, params=params)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as e:
        raise SimbadQueryError(f"Simbad HTTP error: {e.response.status_code}") from e
    except httpx.RequestError as e:
        raise SimbadQueryError(f"Simbad network error: {e}") from e
    except Exception as e:
        raise SimbadQueryError(f"Unexpected error: {e}") from e

    # Parsing the TAP JSON response
    # Format: {"metadata": [...], "data": [[val1, val2, ...]]}
    rows = data.get("data", [])
    if not rows:
        raise SimbadNotFoundError(f"No Simbad result for: {name}")

    row = rows[0]
    cols = [col["name"] for col in data.get("metadata", [])]
    record = dict(zip(cols, row))

    log.info(f"[SIMBAD] Found: {record.get('main_id')} "
             f"RA={record.get('ra')} DEC={record.get('dec')}")

    return StarCreate(
        name=record.get("main_id", name).strip(),
        ra=float(record["ra"]),
        dec=float(record["dec"]),
        magnitude_v=float(record["vmag"]) if record.get("vmag") is not None else None,
        spectral_type=(record.get("sp_type") or "").strip() or None,   # null for many nebulae
    )