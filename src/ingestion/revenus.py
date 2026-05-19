"""
INSEE Local Income (Revenus) Bronze Ingestion
=====================================================
Source  : INSEE – Filosofi / Revenus localisés des ménages (Base IRIS)
Methode : Direct Download in-memory depuis l'archive officielle.

Notes sur le Schema Bronze:
  - L'INSEE masque les données des très petits IRIS avec un "s" (secret statistique).
    Ce script les convertit en NaN.
  - Les latitudes/longitudes ne sont pas présentes dans ce dataset métier.
    Elles sont laissées vides (NaN) et devront être jointes via le référentiel
    géographique IRIS lors du passage en couche Silver.
"""
from __future__ import annotations

import io
import zipfile
import logging
from pathlib import Path

import requests
from bs4 import BeautifulSoup
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

def ingest(year: int = 2021) -> pd.DataFrame:
    """Ingest INSEE income data for Paris IRIS zones."""
    logger = get_logger("revenus", LOG_DIR)

    if year != 2021:
        logger.warning(f"La page INSEE cible est hardcodée pour 2021. L'année {year} risque de ne pas fonctionner sans mise à jour de l'URL source.")

    # 1. Scraping dynamique pour récupérer le lien de l'archive
    page_url = "https://www.insee.fr/fr/statistiques/8229323"
    base_url = "https://www.insee.fr"

    logger.info(f"Fetching INSEE page for Filosofi IRIS {year}...")
    try:
        response = requests.get(page_url, timeout=10)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Failed to fetch INSEE page: {e}")
        return pd.DataFrame(columns=BRONZE_COLUMNS)

    soup = BeautifulSoup(response.text, 'html.parser')
    download_link = None
    for a in soup.find_all('a', href=True):
        href = a['href']
        if '/fichier/8229323/' in href and (href.endswith('.csv') or href.endswith('.zip')):
            download_link = base_url + href if href.startswith('/') else href
            break

    if not download_link:
        logger.error("Download link not found on the INSEE page.")
        return pd.DataFrame(columns=BRONZE_COLUMNS)

    # 2. Téléchargement de l'archive en RAM (In-Memory)
    logger.info(f"Downloading archive from {download_link}...")
    try:
        file_res = requests.get(download_link, timeout=60)
        file_res.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Failed to download archive: {e}")
        return pd.DataFrame(columns=BRONZE_COLUMNS)

    # 3. Extraction et chargement dans Pandas
    logger.info("Extracting and loading CSV into Pandas...")
    try:
        if download_link.endswith('.zip'):
            with zipfile.ZipFile(io.BytesIO(file_res.content)) as z:
                csv_filename = [name for name in z.namelist() if name.endswith('.csv')][0]
                with z.open(csv_filename) as f:
                    df = pd.read_csv(f, sep=';', dtype={'IRIS': str}, low_memory=False)
        else:
            df = pd.read_csv(io.BytesIO(file_res.content), sep=';', dtype={'IRIS': str}, low_memory=False)
    except Exception as e:
        logger.error(f"Failed to parse CSV data: {e}")
        return pd.DataFrame(columns=BRONZE_COLUMNS)

    # 4. Filtrage géographique (Paris : codes INSEE commençant par 751)
    logger.info("Processing data: filtering for Paris IRIS (751xx)...")
    paris_df = df[df['IRIS'].str.startswith('751', na=False)].copy()

    # 5. Mapping vers le Bronze Schema
    # Les noms de colonnes INSEE varient légèrement selon les millésimes, on cible avec la racine métier
    col_med = next((col for col in paris_df.columns if 'DISP_MED' in col), None)
    col_gini = next((col for col in paris_df.columns if 'DISP_GI' in col), None)
    col_pov = next((col for col in paris_df.columns if 'DISP_TP60' in col), None)

    # Transformation
    out_df = pd.DataFrame()
    out_df["iris_code"] = paris_df["IRIS"]
    out_df["commune_code"] = out_df["iris_code"].str[:5]

    # Extraction de l'arrondissement (ex: '75114' -> 14)
    out_df["arrondissement"] = out_df["commune_code"].str[-2:].astype(int)

    # Conversion numérique : gère le secret statistique ("s" devient NaN)
    out_df["median_income"] = pd.to_numeric(paris_df[col_med], errors="coerce") if col_med else pd.NA
    out_df["gini_coefficient"] = pd.to_numeric(paris_df[col_gini], errors="coerce") if col_gini else pd.NA
    out_df["poverty_rate"] = pd.to_numeric(paris_df[col_pov], errors="coerce") if col_pov else pd.NA

    # Métadonnées et champs vides
    out_df["year"] = year
    out_df["latitude"] = pd.NA
    out_df["longitude"] = pd.NA
    out_df["ingested_at"] = pd.Timestamp.utcnow()

    # Réordonnancement final pour matcher le schema
    out_df = out_df[BRONZE_COLUMNS]

    logger.info(f"Ingestion successful: {len(out_df)} IRIS records processed.")

    return out_df