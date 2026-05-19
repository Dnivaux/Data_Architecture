"""
INSEE Local Income (Revenus) Bronze Ingestion
=====================================================
Source  : INSEE – Filosofi / Revenus localisés des ménages (Base IRIS)
Methode : Direct Download in-memory depuis l'archive ZIP officielle.

Notes sur le Schema Bronze:
  - L'INSEE masque les données des très petits IRIS avec un "s" (secret statistique).
    Ce script les convertit en NaN.
"""
from __future__ import annotations

import io
import zipfile
import logging
from pathlib import Path

import requests
import pandas as pd

from .base import get_logger, save_parquet

LOG_DIR = Path(__file__).parents[2] / "logs"

BRONZE_COLUMNS = [
    "iris_code",
    "commune_code",
    "arrondissement",
    "median_income",
    "gini_coefficient",
    "poverty_rate",
    "year",
    "latitude",
    "longitude",
    "ingested_at",
]

# ==============================================================================
# CONFIGURATION SOURCE
# URL hardcodée issue du catalogue INSEE : "Revenus, pauvreté et niveau de vie en 2021"
# Page source (pour maintenance) : https://www.insee.fr/fr/statistiques/8229323
# ID du noeud CMS INSEE : 8229323 (Spécifique au millésime 2021)
# ==============================================================================
INSEE_ZIP_URL = "https://www.insee.fr/fr/statistiques/fichier/8229323/BASE_TD_FILO_IRIS_2021_DEC_CSV.zip"

def check_insee_health() -> bool:
    """Vérifie si l'archive ZIP de l'INSEE est toujours disponible (Évite les erreurs 404)."""
    logger = logging.getLogger("revenus_health")
    try:
        response = requests.head(INSEE_ZIP_URL, timeout=5)
        if response.status_code == 200:
            logger.info("✅ Archive INSEE REACHABLE (HTTP 200).")
            return True
        elif response.status_code == 404:
            logger.error("❌ Archive introuvable (HTTP 404). L'INSEE a probablement renommé ou supprimé le fichier ZIP.")
            return False
        else:
            logger.warning(f"⚠️ Statut HTTP inattendu : {response.status_code}.")
            return False
    except requests.RequestException as e:
        logger.error(f"❌ Impossible de joindre les serveurs de l'INSEE : {e}")
        return False

def ingest(year: int = 2021) -> pd.DataFrame:
    """Ingest INSEE income data for Paris IRIS zones."""
    logger = get_logger("revenus", LOG_DIR)

    # [FIX] Gestion stricte du paramètre 'year'
    if year != 2021:
        logger.error(
            f"Millésime {year} non supporté par ce script. L'URL source est intimement "
            "liée à la publication 2021 (noeud 8229323). Veuillez mettre à jour "
            "le endpoint ou basculer sur l'API BDL pour les années ultérieures."
        )
        return pd.DataFrame(columns=BRONZE_COLUMNS)

    if not check_insee_health():
        logger.error("Health check failed. Annulation de l'ingestion des revenus.")
        return pd.DataFrame(columns=BRONZE_COLUMNS)

    logger.info(f"Downloading archive from {INSEE_ZIP_URL}...")
    try:
        file_res = requests.get(INSEE_ZIP_URL, timeout=60)
        file_res.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Failed to download archive: {e}")
        return pd.DataFrame(columns=BRONZE_COLUMNS)

    logger.info("Extracting ZIP and loading CSV into Pandas...")
    try:
        with zipfile.ZipFile(io.BytesIO(file_res.content)) as z:
            csv_filename = [name for name in z.namelist() if name.endswith('.csv')][0]
            with z.open(csv_filename) as f:
                df = pd.read_csv(f, sep=';', dtype={'IRIS': str}, low_memory=False)
    except Exception as e:
        logger.error(f"Failed to parse CSV data: {e}")
        return pd.DataFrame(columns=BRONZE_COLUMNS)

    # [FIX] Traçabilité des volumes (Counts)
    total_iris_initial = len(df)
    paris_df = df[df['IRIS'].str.startswith('751', na=False)].copy()
    total_paris = len(paris_df)
    total_dropped = total_iris_initial - total_paris

    logger.info(
        f"Filtrage géographique : {total_iris_initial} IRIS chargés -> "
        f"{total_paris} conservés (Paris) -> {total_dropped} hors-périmètre ignorés."
    )

    col_med = next((col for col in paris_df.columns if 'DISP_MED' in col), None)
    col_gini = next((col for col in paris_df.columns if 'DISP_GI' in col), None)
    col_pov = next((col for col in paris_df.columns if 'DISP_TP60' in col), None)

    missing_cols = [name for name, col in [("Revenu Médian", col_med), ("Gini", col_gini), ("Taux Pauvreté", col_pov)] if not col]
    if missing_cols:
        logger.warning(f"Colonnes métier introuvables : {', '.join(missing_cols)}. Remplies par NaN.")

    out_df = pd.DataFrame()
    out_df["iris_code"] = paris_df["IRIS"]
    out_df["commune_code"] = out_df["iris_code"].str[:5]
    out_df["arrondissement"] = out_df["commune_code"].str[-2:].astype(int)

    out_df["median_income"] = pd.to_numeric(paris_df[col_med], errors="coerce") if col_med else pd.NA
    out_df["gini_coefficient"] = pd.to_numeric(paris_df[col_gini], errors="coerce") if col_gini else pd.NA
    out_df["poverty_rate"] = pd.to_numeric(paris_df[col_pov], errors="coerce") if col_pov else pd.NA

    out_df["year"] = year
    out_df["latitude"] = pd.NA
    out_df["longitude"] = pd.NA
    out_df["ingested_at"] = pd.Timestamp.utcnow()

    out_df = out_df[BRONZE_COLUMNS]

    logger.info(f"Ingestion successful: {len(out_df)} IRIS records ready for Bronze.")

    return out_df

if __name__ == "__main__":
    df = ingest()
    if not df.empty:
        print(df.head())