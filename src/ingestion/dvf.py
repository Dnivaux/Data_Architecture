"""
DVF Bronze Ingestion — Fichier local
=====================================
Source  : data/bronze/dvf/dvf.csv  (base nationale DVF, récupérée manuellement)
Docs    : https://www.data.gouv.fr/fr/datasets/demandes-de-valeurs-foncieres/

Lit le CSV national par chunks pour éviter la saturation RAM, filtre
uniquement les transactions parisiennes (Appartements et Maisons),
et persiste le résultat en Parquet dans data/bronze/dvf_clean/.

Bronze schema (colonnes écrites en Parquet)
-------------------------------------------
id_mutation             str      identifiant unique de la transaction
date_mutation           date     date de la transaction
valeur_fonciere         float    prix de vente (€)
adresse_numero          str
adresse_nom_voie        str
code_postal             str
code_commune            str      code INSEE commune
nom_commune             str
code_departement        str
type_local              str      "Appartement" ou "Maison"
surface_reelle_bati     float    surface bâtie (m²)
surface_terrain         float    surface terrain (m²)
nombre_pieces_principales int
latitude                float
longitude               float
nature_mutation         str      ex. "Vente"
ingested_at             datetime horodatage UTC de l'ingestion
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .base import BRONZE_ROOT, get_logger, save_parquet

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

CSV_PATH = BRONZE_ROOT / "dvf" / "dvf.csv"
LOG_DIR = Path(__file__).parents[2] / "logs"

CHUNK_SIZE = 200_000

VALID_TYPES = {"Appartement", "Maison"}

# Colonnes CSV source lues (évite de charger les colonnes inutiles)
CSV_USECOLS = [
    "id_mutation",
    "date_mutation",
    "nature_mutation",
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
]

# Contrat Bronze (colonnes écrites en Parquet — doit rester stable pour le Silver)
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

# Typage explicite pour accélérer la lecture et réduire la mémoire
CSV_DTYPE: dict[str, Any] = {
    "id_mutation":               "string",
    "nature_mutation":           "string",
    "adresse_numero":            "string",
    "adresse_nom_voie":          "string",
    "code_postal":               "string",
    "code_commune":              "string",
    "nom_commune":               "string",
    "code_departement":          "string",
    "type_local":                "string",
    "valeur_fonciere":           "string",   # virgule décimale → nettoyé ci-dessous
    "surface_reelle_bati":       "float32",
    "surface_terrain":           "float32",
    "nombre_pieces_principales": "Int16",    # nullable integer
    "latitude":                  "float32",
    "longitude":                 "float32",
}


# ---------------------------------------------------------------------------
# Helpers internes
# ---------------------------------------------------------------------------

def _clean_chunk(chunk: pd.DataFrame, ingested_at: datetime) -> pd.DataFrame:
    """Filtre et nettoie un chunk : Paris uniquement, Appartements et Maisons."""
    # Filtrage Paris (département 75)
    chunk = chunk[chunk["code_departement"] == "75"]
    if chunk.empty:
        return chunk

    # Filtrage types de locaux pertinents
    chunk = chunk[chunk["type_local"].isin(VALID_TYPES)]
    if chunk.empty:
        return chunk

    # Nettoyage valeur_fonciere (séparateur décimal peut être une virgule)
    chunk["valeur_fonciere"] = (
        chunk["valeur_fonciere"]
        .str.replace(",", ".", regex=False)
        .pipe(pd.to_numeric, errors="coerce")
        .astype("float64")
    )

    # Suppression des ventes sans prix renseigné
    chunk = chunk[chunk["valeur_fonciere"].notna() & (chunk["valeur_fonciere"] > 0)]
    if chunk.empty:
        return chunk

    # Conversion date
    chunk["date_mutation"] = pd.to_datetime(chunk["date_mutation"], errors="coerce")

    # Ajout timestamp d'ingestion
    chunk["ingested_at"] = ingested_at

    return chunk[BRONZE_COLUMNS]


# ---------------------------------------------------------------------------
# Point d'entrée principal
# ---------------------------------------------------------------------------

def ingest(
    csv_path: str | Path | None = None,
    postal_codes: list[str] | None = None,
) -> pd.DataFrame:
    """
    Ingère les transactions DVF parisiennes depuis le fichier CSV local.

    Parameters
    ----------
    csv_path : str | Path, optional
        Chemin vers le CSV source. Par défaut : data/bronze/dvf/dvf.csv.
    postal_codes : list[str], optional
        Filtre optionnel sur les codes postaux (ex. ["75001", "75008"]).
        Par défaut : tous les arrondissements parisiens.

    Returns
    -------
    pd.DataFrame
        DataFrame filtré et nettoyé (également persisté en Parquet).
    """
    logger = get_logger("dvf", LOG_DIR)
    source = Path(csv_path) if csv_path else CSV_PATH

    if not source.exists():
        raise FileNotFoundError(
            f"Fichier DVF introuvable : {source}\n"
            "Placez le CSV national DVF dans data/bronze/dvf/dvf.csv."
        )

    ingested_at = datetime.now(timezone.utc)
    run_date = date.today().isoformat()

    logger.info("DVF ingestion locale démarrée — source : %s", source)
    logger.info("Taille fichier : %.1f Mo", source.stat().st_size / 1_048_576)

    chunks_kept: list[pd.DataFrame] = []
    total_read = 0
    total_kept = 0

    try:
        reader = pd.read_csv(
            source,
            usecols=CSV_USECOLS,
            dtype=CSV_DTYPE,
            chunksize=CHUNK_SIZE,
            low_memory=False,
        )

        for i, chunk in enumerate(reader):
            total_read += len(chunk)
            cleaned = _clean_chunk(chunk, ingested_at)
            if not cleaned.empty:
                chunks_kept.append(cleaned)
                total_kept += len(cleaned)
            if (i + 1) % 10 == 0:
                logger.debug(
                    "  Chunk %d — lus : %d | conservés Paris : %d",
                    i + 1, total_read, total_kept,
                )

    except Exception as exc:
        logger.error("Erreur lors de la lecture du CSV DVF : %s", exc, exc_info=True)
        raise

    if not chunks_kept:
        logger.warning("DVF ingestion : aucune transaction parisienne trouvée.")
        return pd.DataFrame(columns=BRONZE_COLUMNS)

    df = pd.concat(chunks_kept, ignore_index=True)

    # Filtre optionnel sur les codes postaux
    if postal_codes:
        df = df[df["code_postal"].isin(postal_codes)]
        logger.info("Filtre codes postaux appliqué : %d codes → %d lignes", len(postal_codes), len(df))

    logger.info(
        "DVF ingestion : %d lignes nationales lues → %d transactions parisiennes conservées",
        total_read, len(df),
    )

    # Sauvegarde Parquet (source = dvf_clean pour distinguer du CSV brut)
    path = save_parquet(
        df,
        source="dvf_clean",
        partition_col="date",
        partition_value=run_date,
        filename="part-0.parquet",
    )
    logger.info("Parquet sauvegardé → %s", path)

    return df
