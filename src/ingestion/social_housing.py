"""
Social Housing Bronze Ingestion
================================
Source  : Fichier local téléchargé depuis Paris Open Data
          "Logements sociaux financés à Paris"
Input   : data/bronze/logement_sociaux/logements-sociaux-finances-a-paris.parquet

Standardise le fichier brut en schéma Bronze normalisé et le persiste
sous data/bronze/social_housing/part-0.parquet.

Bronze schema
-------------
arrondissement      int      arrondissement parisien (1–20)
annee               int      année de financement/agrément
nombre_logements    int      nombre total de logements sociaux financés
ingested_at         datetime UTC timestamp de l'ingestion
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .base import BRONZE_ROOT, get_logger, save_parquet

RAW_PATH = Path(__file__).parents[2] / "data" / "bronze" / "logement_sociaux" / "logements-sociaux-finances-a-paris.parquet"
LOG_DIR = Path(__file__).parents[2] / "logs"

BRONZE_COLUMNS = ["arrondissement", "annee", "nombre_logements", "ingested_at"]


def _parse_arrondissement(raw: pd.Series) -> pd.Series:
    """
    Accepte plusieurs formats possibles :
      - int64 already clean (1-20)
      - str "75012" → 12
      - str "Paris 12e" / "Paris 12ème" → 12
    """
    if pd.api.types.is_integer_dtype(raw):
        return raw.astype(int)

    cleaned = raw.astype(str).str.strip()
    # format "750xx"
    mask_insee = cleaned.str.match(r"^750\d{2}$")
    result = pd.Series(index=raw.index, dtype="Int64")
    result[mask_insee] = cleaned[mask_insee].str[-2:].astype(int)

    # format "Paris 12e" ou libre texte numérique
    mask_text = ~mask_insee
    extracted = cleaned[mask_text].str.extract(r"(\d{1,2})", expand=False)
    result[mask_text] = pd.to_numeric(extracted, errors="coerce").astype("Int64")

    return result


def ingest(raw_path: Path = RAW_PATH) -> pd.DataFrame:
    """
    Charge, nettoie et persiste les données logements sociaux.

    Returns
    -------
    pd.DataFrame
        DataFrame normalisé (également écrit en Parquet).
    """
    logger = get_logger("social_housing", LOG_DIR)
    ingested_at = datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # 1. Chargement
    # ------------------------------------------------------------------
    if not raw_path.exists():
        logger.error("Fichier source introuvable : %s", raw_path)
        return pd.DataFrame(columns=BRONZE_COLUMNS)

    logger.info("Chargement du fichier brut : %s", raw_path)
    df_raw = pd.read_parquet(raw_path, engine="pyarrow")
    logger.info("Fichier chargé — %d lignes, %d colonnes", *df_raw.shape)

    # ------------------------------------------------------------------
    # 2. Filtrage Paris (sécurité, toutes les lignes sont déjà Paris)
    # ------------------------------------------------------------------
    if "ville" in df_raw.columns:
        before = len(df_raw)
        df_raw = df_raw[df_raw["ville"].str.strip().str.lower() == "paris"].copy()
        dropped = before - len(df_raw)
        if dropped:
            logger.warning("  %d lignes hors Paris écartées", dropped)
        else:
            logger.info("  Toutes les lignes sont déjà Paris — aucun filtrage nécessaire")

    # ------------------------------------------------------------------
    # 3. Normalisation des colonnes clés
    # ------------------------------------------------------------------
    df = pd.DataFrame()

    # arrondissement
    df["arrondissement"] = _parse_arrondissement(df_raw["arrdt"])
    invalid_arrdt = df["arrondissement"].isna() | ~df["arrondissement"].between(1, 20)
    if invalid_arrdt.any():
        logger.warning("  %d lignes avec arrondissement invalide/hors-Paris écartées", invalid_arrdt.sum())
    df = df[~invalid_arrdt].copy()
    df["arrondissement"] = df["arrondissement"].astype(int)

    # annee : datetime.date → int année
    annee_raw = df_raw.loc[df.index, "annee"]
    if pd.api.types.is_object_dtype(annee_raw) or hasattr(annee_raw.iloc[0], "year"):
        df["annee"] = pd.to_datetime(annee_raw, errors="coerce").dt.year.astype("Int64")
    else:
        df["annee"] = pd.to_numeric(annee_raw, errors="coerce").astype("Int64")

    null_annee = df["annee"].isna().sum()
    if null_annee:
        logger.warning("  %d lignes avec annee nulle — conservées", null_annee)
    df["annee"] = df["annee"].astype("Int64")

    # nombre_logements
    df["nombre_logements"] = pd.to_numeric(df_raw.loc[df.index, "nb_logmt_total"], errors="coerce").astype("Int64")
    neg = (df["nombre_logements"] < 0).sum()
    if neg:
        logger.warning("  %d lignes avec nombre_logements négatif (données sources) — conservées", neg)

    # ingested_at
    df["ingested_at"] = ingested_at

    df = df[BRONZE_COLUMNS].reset_index(drop=True)

    # ------------------------------------------------------------------
    # 4. Persistance Parquet
    # ------------------------------------------------------------------
    path = save_parquet(df, source="social_housing", filename="part-0.parquet")
    logger.info("Ingestion terminée — %d lignes sauvegardées → %s", len(df), path)

    return df


if __name__ == "__main__":
    result = ingest()
    print("\n--- Aperçu du DataFrame Bronze ---")
    print(result.head(10).to_string(index=False))
    print(f"\nShape : {result.shape}")
    print(f"\nTypes :\n{result.dtypes}")
