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

import csv
import logging
import os
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

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

def _normalize_col(name: str) -> str:
    text = unicodedata.normalize("NFKD", str(name))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.strip().lower()
    text = "".join(ch if ch.isalnum() else "_" for ch in text)
    while "__" in text:
        text = text.replace("__", "_")
    return text.strip("_")


COLUMN_ALIASES = {
    "id_mutation": "id_mutation",
    "date_mutation": "date_mutation",
    "valeur_fonciere": "valeur_fonciere",
    "adresse_numero": "adresse_numero",
    "adresse_nom_voie": "adresse_nom_voie",
    "code_postal": "code_postal",
    "code_commune": "code_commune",
    "nom_commune": "nom_commune",
    "code_departement": "code_departement",
    "type_local": "type_local",
    "surface_reelle_bati": "surface_reelle_bati",
    "surface_terrain": "surface_terrain",
    "nombre_pieces_principales": "nombre_pieces_principales",
    "latitude": "latitude",
    "longitude": "longitude",
    "nature_mutation": "nature_mutation",
    # Common DVF CSV labels
    "id_mutation": "id_mutation",
    "date_mutation": "date_mutation",
    "valeur_fonciere": "valeur_fonciere",
    "adresse_numero": "adresse_numero",
    "adresse_nom_voie": "adresse_nom_voie",
    "code_postal": "code_postal",
    "code_commune": "code_commune",
    "nom_commune": "nom_commune",
    "code_departement": "code_departement",
    "type_local": "type_local",
    "surface_reelle_bati": "surface_reelle_bati",
    "surface_terrain": "surface_terrain",
    "nombre_pieces_principales": "nombre_pieces_principales",
    "latitude": "latitude",
    "longitude": "longitude",
    "nature_mutation": "nature_mutation",
    # Variants with spaces/accents
    "valeur_fonciere_euros": "valeur_fonciere",
    "surface_reelle_bati_m2": "surface_reelle_bati",
    "surface_terrain_m2": "surface_terrain",
    "nombre_pieces_principales": "nombre_pieces_principales",
    "adresse_nom_voie": "adresse_nom_voie",
    "adresse_numero": "adresse_numero",
    "nature_mutation": "nature_mutation",
}


def _normalize_postal(series: pd.Series) -> pd.Series:
    values = series.astype(str).str.replace(r"\.0$", "", regex=True)
    return values.str.zfill(5)


def _detect_csv_format(csv_path: str) -> tuple[str, str]:
    with open(csv_path, "rb") as handle:
        sample = handle.read(65536)
    try:
        text = sample.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        text = sample.decode("latin1")
        encoding = "latin1"

    sniffer = csv.Sniffer()
    try:
        dialect = sniffer.sniff(text, delimiters=[",", ";", "\t", "|"])
        sep = dialect.delimiter
    except csv.Error:
        sep = ";" if ";" in text else ","

    return sep, encoding


def ingest(
    csv_path: str | Path | None = None,
    postal_codes: list[str] | None = None,
    date_min: str | None = None,
    date_max: str | None = None,
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
    date_min : str, optional
        Ignoré pour le CSV local (gardé pour compatibilité pipeline).
    date_max : str, optional
        Ignoré pour le CSV local (gardé pour compatibilité pipeline).

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


def ingest_from_csv(
    csv_path: str,
    chunksize: int = 200_000,
    date_value: str | None = None,
    encoding: str | None = None,
    sep: str | None = None,
) -> None:
    """
    Stream a large DVF CSV and write Parquet partitions per arrondissement.

    The CSV is processed in chunks to avoid loading the full file into memory.
    Only Paris (postal codes 75001–75020) is kept.
    """
    logger = get_logger("dvf", LOG_DIR)
    csv_path = os.path.abspath(csv_path)
    run_date = date_value or date.today().isoformat()
    ingested_at = datetime.now(timezone.utc)

    if not os.path.exists(csv_path):
        raise FileNotFoundError(csv_path)

    logger.info("DVF CSV ingestion started — %s", csv_path)

    if sep is None or encoding is None:
        detected_sep, detected_encoding = _detect_csv_format(csv_path)
        sep = sep or detected_sep
        encoding = encoding or detected_encoding

    logger.info("DVF CSV format — sep=%s encoding=%s", sep, encoding)

    # Read header only to align columns without loading the full file.
    header = pd.read_csv(csv_path, nrows=0, encoding=encoding, sep=sep)
    csv_cols = header.columns.tolist()
    logger.debug("DVF CSV columns: %s", csv_cols)

    out_dir = BRONZE_ROOT / "dvf" / f"date={run_date}"
    out_dir.mkdir(parents=True, exist_ok=True)

    writers: dict[int, pq.ParquetWriter] = {}
    schema: pa.Schema | None = None

    def get_writer(arrond: int) -> pq.ParquetWriter:
        nonlocal schema
        if arrond in writers:
            return writers[arrond]
        out_path = out_dir / f"arrond_{arrond:02d}.parquet"
        writer = pq.ParquetWriter(out_path, schema)
        writers[arrond] = writer
        return writer

    for chunk in pd.read_csv(
        csv_path,
        chunksize=chunksize,
        encoding=encoding,
        sep=sep,
        low_memory=False,
    ):
        # Normalize column names to canonical schema.
        rename_map = {}
        for col in chunk.columns:
            norm = _normalize_col(col)
            if norm in COLUMN_ALIASES:
                rename_map[col] = COLUMN_ALIASES[norm]
        if rename_map:
            chunk = chunk.rename(columns=rename_map)

        # Keep only needed columns when present.
        for col in BRONZE_COLUMNS:
            if col not in chunk.columns:
                chunk[col] = None

        chunk = chunk[BRONZE_COLUMNS]

        # Filter Paris postal codes.
        postal = _normalize_postal(chunk["code_postal"])
        chunk = chunk[postal.str.startswith("750")]
        if chunk.empty:
            continue

        chunk["valeur_fonciere"] = pd.to_numeric(chunk["valeur_fonciere"].astype(str).str.replace(",", "."), errors="coerce")
        chunk["surface_reelle_bati"] = pd.to_numeric(chunk["surface_reelle_bati"].astype(str).str.replace(",", "."), errors="coerce")
        chunk["surface_terrain"] = pd.to_numeric(chunk["surface_terrain"].astype(str).str.replace(",", "."), errors="coerce")
        chunk["nombre_pieces_principales"] = pd.to_numeric(chunk["nombre_pieces_principales"], errors="coerce")
        chunk["date_mutation"] = pd.to_datetime(chunk["date_mutation"], errors="coerce")
        chunk["ingested_at"] = ingested_at

        if schema is None:
            schema = pa.Schema.from_pandas(chunk, preserve_index=False)

        postal = _normalize_postal(chunk["code_postal"])
        for arrond, group in chunk.groupby(postal.str[-2:]):
            if not arrond.isdigit():
                continue
            writer = get_writer(int(arrond))
            table = pa.Table.from_pandas(group, schema=schema, preserve_index=False)
            writer.write_table(table)

    for writer in writers.values():
        writer.close()

    logger.info("DVF CSV ingestion complete — %d arrondissement files", len(writers))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ingest DVF data.")
    parser.add_argument("--csv", dest="csv_path", help="Path to DVF CSV to convert")
    parser.add_argument("--chunksize", type=int, default=200_000)
    parser.add_argument("--date", dest="date_value", default=None)
    parser.add_argument("--encoding", default=None)
    parser.add_argument("--sep", default=None)
    args = parser.parse_args()

    if args.csv_path:
        ingest_from_csv(
            args.csv_path,
            chunksize=args.chunksize,
            date_value=args.date_value,
            encoding=args.encoding,
            sep=args.sep,
        )
    else:
        ingest()
