"""
DVF Bronze Ingestion
====================
Source  : IGN Apicarto – Demandes de Valeurs Foncières
Endpoint: https://apicarto.ign.fr/api/dvf/mutation
Docs    : https://apicarto.ign.fr/api/doc/dvf

Fetches all property transactions for the 20 Paris arrondissements
and saves one partitioned Parquet file per arrondissement per run-date.

Bronze schema (columns written to Parquet)
------------------------------------------
id_mutation             str      unique transaction id
date_mutation           date     transaction date
valeur_fonciere         float    sale price (€)
adresse_numero          str
adresse_nom_voie        str
code_postal             str
code_commune            str      INSEE commune code
nom_commune             str
code_departement        str
type_local              str      e.g. "Appartement", "Maison"
surface_reelle_bati     float    built area (m²)
surface_terrain         float    land area (m²)
nombre_pieces_principales int
latitude                float
longitude               float
nature_mutation         str      e.g. "Vente"
ingested_at             datetime UTC timestamp of ingestion run
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .base import BRONZE_ROOT, build_session, get_logger, save_parquet

BASE_URL = "https://apicarto.ign.fr/api/dvf/mutation"
PAGE_SIZE = 500

# Paris postal codes 75001–75020
PARIS_POSTAL_CODES = [f"750{str(i).zfill(2)}" for i in range(1, 21)]

LOG_DIR = Path(__file__).parents[2] / "logs"

# Canonical output columns (Bronze contract)
BRONZE_COLUMNS = [
    "id_mutation",
    "date_mutation",
    "valeur_fonciere",
    "adresse_numero",
    "adresse_nom_voie",
    "code_postal",
    "code_commune",
    "nom_commune",
    "code_departement",
    "type_local",
    "surface_reelle_bati",
    "surface_terrain",
    "nombre_pieces_principales",
    "latitude",
    "longitude",
    "nature_mutation",
    "ingested_at",
]


def _fetch_page(
    session: Any,
    logger: logging.Logger,
    code_postal: str,
    date_min: str,
    date_max: str,
    offset: int,
) -> list[dict]:
    """Fetch one page of DVF mutations for a given postal code and date range."""
    params = {
        "code_postal": code_postal,
        "date_mutation_min": date_min,
        "date_mutation_max": date_max,
        "offset": offset,
        "limit": PAGE_SIZE,
    }
    resp = session.get(BASE_URL, params=params)

    if resp.status_code != 200:
        logger.warning(
            "DVF %s offset=%d → HTTP %d: %s",
            code_postal, offset, resp.status_code, resp.text[:200],
        )
        return []

    payload = resp.json()
    return payload.get("features", [])


def _feature_to_row(feature: dict, ingested_at: datetime) -> dict:
    """Flatten a GeoJSON feature into a Bronze row dict."""
    props = feature.get("properties", {})
    geom = feature.get("geometry") or {}
    coords = geom.get("coordinates") if geom.get("type") == "Point" else None

    return {
        "id_mutation": props.get("id_mutation"),
        "date_mutation": props.get("date_mutation"),
        "valeur_fonciere": _to_float(props.get("valeur_fonciere")),
        "adresse_numero": props.get("adresse_numero"),
        "adresse_nom_voie": props.get("adresse_nom_voie"),
        "code_postal": props.get("code_postal"),
        "code_commune": props.get("code_commune"),
        "nom_commune": props.get("nom_commune"),
        "code_departement": props.get("code_departement"),
        "type_local": props.get("type_local"),
        "surface_reelle_bati": _to_float(props.get("surface_reelle_bati")),
        "surface_terrain": _to_float(props.get("surface_terrain")),
        "nombre_pieces_principales": _to_int(props.get("nombre_pieces_principales")),
        "latitude": coords[1] if coords and len(coords) >= 2 else None,
        "longitude": coords[0] if coords and len(coords) >= 2 else None,
        "nature_mutation": props.get("nature_mutation"),
        "ingested_at": ingested_at,
    }


def _to_float(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", ".")) if value is not None else None
    except (ValueError, TypeError):
        return None


def _to_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (ValueError, TypeError):
        return None


def ingest(
    date_min: str | None = None,
    date_max: str | None = None,
    postal_codes: list[str] | None = None,
) -> pd.DataFrame:
    """
    Ingest DVF transactions for Paris into the Bronze layer.

    Parameters
    ----------
    date_min : str, optional
        ISO date string, e.g. "2023-01-01". Defaults to Jan 1 of current year.
    date_max : str, optional
        ISO date string. Defaults to today.
    postal_codes : list[str], optional
        Subset of Paris postal codes to ingest. Defaults to all 20.

    Returns
    -------
    pd.DataFrame
        Combined DataFrame of all ingested rows (also written to Parquet).
    """
    logger = get_logger("dvf", LOG_DIR)
    today = date.today()
    date_min = date_min or f"{today.year}-01-01"
    date_max = date_max or today.isoformat()
    codes = postal_codes or PARIS_POSTAL_CODES
    ingested_at = datetime.now(timezone.utc)
    run_date = today.isoformat()

    session = build_session()
    all_rows: list[dict] = []

    logger.info("DVF ingestion started — %s to %s — %d arrondissements", date_min, date_max, len(codes))

    for code in codes:
        arrond = int(code[-2:])  # 75001 → 1
        logger.info("  Fetching arrondissement %02d (postal=%s)", arrond, code)
        offset = 0
        arrond_rows: list[dict] = []

        while True:
            features = _fetch_page(session, logger, code, date_min, date_max, offset)
            if not features:
                break
            for feat in features:
                arrond_rows.append(_feature_to_row(feat, ingested_at))
            logger.debug("    offset=%d → %d features", offset, len(features))
            if len(features) < PAGE_SIZE:
                break
            offset += PAGE_SIZE

        if arrond_rows:
            df_arrond = pd.DataFrame(arrond_rows)[BRONZE_COLUMNS]
            df_arrond["date_mutation"] = pd.to_datetime(df_arrond["date_mutation"], errors="coerce")
            path = save_parquet(
                df_arrond,
                source="dvf",
                partition_col="date",
                partition_value=run_date,
                filename=f"arrond_{arrond:02d}.parquet",
            )
            logger.info("    Saved %d rows → %s", len(df_arrond), path)
            all_rows.extend(arrond_rows)
        else:
            logger.warning("    No data returned for arrondissement %02d", arrond)

    if not all_rows:
        logger.warning("DVF ingestion produced no rows.")
        return pd.DataFrame(columns=BRONZE_COLUMNS)

    df_all = pd.DataFrame(all_rows)[BRONZE_COLUMNS]
    df_all["date_mutation"] = pd.to_datetime(df_all["date_mutation"], errors="coerce")
    logger.info("DVF ingestion complete — %d total rows", len(df_all))
    return df_all
