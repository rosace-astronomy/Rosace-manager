"""
Interrogation de la base Simbad (CDS Strasbourg) via l'API TAP.
Retourne les informations d'une étoile sous forme de StarCreate.
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
    """L'objet n'a pas été trouvé dans Simbad."""
    pass


class SimbadQueryError(Exception):
    """Erreur lors de la requête à Simbad."""
    pass


async def simbad_search(name: str) -> StarCreate:
    """
    Interroge Simbad pour un nom d'étoile et retourne un StarCreate.

    :param name: Nom de l'étoile (ex. "Vega", "Sirius", "HD 187642")
    :raises SimbadNotFoundError: Si l'étoile n'est pas trouvée
    :raises SimbadQueryError: En cas d'erreur de communication
    """
    query = ADQL_QUERY.format(name=name.replace("'", "''"))  # échappement SQL

    params = {
        "REQUEST": "doQuery",
        "LANG":    "ADQL",
        "FORMAT":  "json",
        "QUERY":   query,
    }

    log.info(f"[SIMBAD] Recherche : {name}")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(SIMBAD_TAP_URL, params=params)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as e:
        raise SimbadQueryError(f"Erreur HTTP Simbad : {e.response.status_code}") from e
    except httpx.RequestError as e:
        raise SimbadQueryError(f"Erreur réseau Simbad : {e}") from e
    except Exception as e:
        raise SimbadQueryError(f"Erreur inattendue : {e}") from e

    # Parsing de la réponse JSON TAP
    # Format : {"metadata": [...], "data": [[val1, val2, ...]]}
    rows = data.get("data", [])
    if not rows:
        raise SimbadNotFoundError(f"Aucun résultat Simbad pour : {name}")

    row = rows[0]
    cols = [col["name"] for col in data.get("metadata", [])]
    record = dict(zip(cols, row))

    log.info(f"[SIMBAD] Trouvé : {record.get('main_id')} "
             f"RA={record.get('ra')} DEC={record.get('dec')}")

    return StarCreate(
        name=record.get("main_id", name).strip(),
        ra=float(record["ra"]),
        dec=float(record["dec"]),
        magnitude=float(record["vmag"]) if record.get("vmag") is not None else None,
        spectral_type=record.get("sp_type", "").strip() or None,
    )
