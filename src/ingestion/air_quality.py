"""
Airparif Air Quality (Indice ATMO) Bronze Ingestion
==========================================================
Source  : Airparif API - /indices/prevision/commune
API     : https://api.airparif.asso.fr/docs

Implémentation :
  1. Utilisation de la variable d'environnement AIRPARIF_API_KEY.
  2. Requête GET 'bulk' pour les codes INSEE de Paris (75101 à 75120).
  3. Flattening du JSON imbriqué en lignes de prévisions journalières.
  4. Normalisation stricte sur le schéma Bronze défini.

Bronze schema
-----------------------
commune_code        str      INSEE identifier (e.g., '75114')
arrondissement      int      Extracted from commune_code (e.g., 14)
date_prevision      date     Forecast date (YYYY-MM-DD)
indice_atmo         int      Numerical ATMO index (1: Good -> 6: Extremely bad)
qualificatif        str      Textual description (e.g., 'Moyen', 'Dégradé')
ingested_at         datetime Timestamp of pipeline execution
"""
from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import requests

from .base import get_logger

LOG_DIR = Path(__file__).parents[2] / "logs"

BRONZE_COLUMNS = [
    "commune_code",
    "arrondissement",
    "date_prevision",
    "indice_atmo",
    "qualificatif",
    "ingested_at"
]

def ingest() -> pd.DataFrame:
    """Ingest Airparif forecast data for Paris arrondissements."""
    logger = get_logger("airparif", LOG_DIR)

    # 1. Vérification de la clé API (TODO 1)
    api_key = os.environ.get("AIRPARIF_API_KEY")
    if not api_key:
        logger.error("AIRPARIF_API_KEY environment variable is missing. Aborting ingestion.")
        return pd.DataFrame(columns=BRONZE_COLUMNS)

    # 2. Préparation et exécution de la requête Bulk (TODO 2)
    # Génère "75101,75102,...,75120"
    paris_insee_codes = [f"751{str(i).zfill(2)}" for i in range(1, 21)]
    insee_params = ",".join(paris_insee_codes)

    endpoint_url = "https://api.airparif.asso.fr/indices/prevision/commune"
    headers = {
        "X-Api-Key": api_key,
        "Accept": "application/json"
    }

    logger.info(f"Fetching Airparif API for {len(paris_insee_codes)} communes in bulk...")
    try:
        response = requests.get(
            endpoint_url,
            headers=headers,
            params={"insee": insee_params},
            timeout=15
        )
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Airparif API request failed: {e}")
        return pd.DataFrame(columns=BRONZE_COLUMNS)

    try:
        data = response.json()
    except ValueError as e:
        logger.error(f"Failed to parse JSON response: {e}")
        return pd.DataFrame(columns=BRONZE_COLUMNS)

    # 3. Flattening du JSON (TODO 3)
    logger.info("Flattening JSON response into daily records...")
    records = []
    ingestion_time = pd.Timestamp.utcnow()

    for insee, previsions in data.items():
        if not isinstance(previsions, list):
            continue

        for prev in previsions:
            records.append({
                "commune_code": insee,
                "arrondissement": int(insee[-2:]),  # Extraction de l'arrondissement ('75114' -> 14)
                "date_prevision": prev.get("date"),
                "indice_atmo": prev.get("valeur"),
                "qualificatif": prev.get("qualificatif"),
                "ingested_at": ingestion_time
            })

    # 4. Normalisation sur le schéma Bronze (TODO 4)
    out_df = pd.DataFrame(records)

    if out_df.empty:
        logger.warning("No valid forecast data found in the API response.")
        return pd.DataFrame(columns=BRONZE_COLUMNS)

    # Cast strict des types pandas
    out_df["date_prevision"] = pd.to_datetime(out_df["date_prevision"], errors="coerce").dt.date
    out_df["indice_atmo"] = pd.to_numeric(out_df["indice_atmo"], errors="coerce").astype("Int64") # Int64 gère les NaN proprement

    # Traçabilité : log des conversions échouées
    nan_count = out_df["indice_atmo"].isna().sum()
    if nan_count > 0:
        logger.warning(f"Data quality issue: {nan_count} records with missing indice_atmo after conversion.")

    # Réordonnancement selon la constante BRONZE_COLUMNS
    out_df = out_df[BRONZE_COLUMNS]

    logger.info(f"Ingestion successful: {len(out_df)} forecast records processed.")

    return out_df

if __name__ == "__main__":
    # Test unitaire rapide en local
    # Assure-toi d'avoir fait : export AIRPARIF_API_KEY="ta_cle_api" dans ton terminal
    df = ingest()
    if not df.empty:
        print(df.head())
        print(df.dtypes)