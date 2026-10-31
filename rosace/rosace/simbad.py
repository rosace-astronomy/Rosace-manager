# SPDX-FileCopyrightText: 2026 The ROSACE project
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Queries the Simbad database via the TAP API.
Returns a star's information as a StarCreate.

Several Simbad servers answer the same queries (the CDS in Strasbourg, its
mirror at Harvard...): they are tried in the order of the configuration
(SIMBAD_TAP_URLS), the first one being the preferred one.
"""

import logging
from typing import Optional
from urllib.parse import urlparse

import httpx

from .config import SIMBAD_TAP_URLS
from .targets import StarCreate

log = logging.getLogger("spectro.simbad")

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


class SimbadServers:
    """The Simbad servers of one search, possibly of several stars (an import
    list): the first configured server is tried first; once a server has
    answered, the next stars of the same search start with it, so that a
    server that is down costs one timeout per search, not one per star. A new
    search (a new instance) starts again from the first configured server."""

    def __init__(self, urls: Optional[list[str]] = None):
        self.urls = list(urls if urls is not None else SIMBAD_TAP_URLS)
        self.answered = 0  # index of the server that answered last

    def order(self) -> list[str]:
        return [self.urls[self.answered]] + [u for i, u in enumerate(self.urls) if i != self.answered]

    def record_answer(self, url: str) -> None:
        self.answered = self.urls.index(url)


async def _query(url: str, params: dict) -> dict:
    """One TAP query to one server; raises SimbadQueryError if it does not
    give a readable answer."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        raise SimbadQueryError(f"HTTP error {e.response.status_code}") from e
    except httpx.RequestError as e:
        raise SimbadQueryError(f"network error: {e!r}") from e
    except Exception as e:
        raise SimbadQueryError(f"unexpected error: {e!r}") from e


async def simbad_search(name: str, servers: Optional[SimbadServers] = None) -> StarCreate:
    """
    Queries Simbad for a star name and returns a StarCreate.

    :param name: Star name (e.g. "Vega", "Sirius", "HD 187642")
    :param servers: the servers of the current search, shared by the stars of
        an import list; None for a search of its own (first server first)
    :raises SimbadNotFoundError: If the star isn't found — an answer, so the
        other servers are not asked
    :raises SimbadQueryError: If no server gives a readable answer
    """
    servers = servers or SimbadServers()
    query = ADQL_QUERY.format(name=name.replace("'", "''"))  # SQL escaping

    params = {
        "REQUEST": "doQuery",
        "LANG":    "ADQL",
        "FORMAT":  "json",
        "QUERY":   query,
    }

    log.info(f"[SIMBAD] Querying: {name}")

    failures = []
    data = None
    order = servers.order()
    for i, url in enumerate(order):
        try:
            data = await _query(url, params)
        except SimbadQueryError as e:
            failures.append(f"{urlparse(url).hostname}: {e}")
            if i + 1 < len(order):
                log.warning(f"[SIMBAD] {url} did not answer ({e}); trying {order[i + 1]}")
            continue
        servers.record_answer(url)
        break
    if data is None:
        raise SimbadQueryError("No Simbad server answered — " + "; ".join(failures))
    server = urlparse(url).hostname

    # Parsing the TAP JSON response
    # Format: {"metadata": [...], "data": [[val1, val2, ...]]}
    rows = data.get("data", [])
    if not rows:
        raise SimbadNotFoundError(f"No Simbad result for: {name}")

    row = rows[0]
    cols = [col["name"] for col in data.get("metadata", [])]
    record = dict(zip(cols, row))

    log.info(f"[SIMBAD] Found: {record.get('main_id')} "
             f"RA={record.get('ra')} DEC={record.get('dec')} ({server})")

    return StarCreate(
        name=record.get("main_id", name).strip(),
        ra=float(record["ra"]),
        dec=float(record["dec"]),
        magnitude_v=float(record["vmag"]) if record.get("vmag") is not None else None,
        spectral_type=(record.get("sp_type") or "").strip() or None,   # null for many nebulae
    )