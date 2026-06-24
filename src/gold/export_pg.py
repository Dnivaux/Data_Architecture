"""
Gold → PostgreSQL Export
========================
Lit les tables Gold (Parquet) et les exporte dans PostgreSQL via SQLAlchemy
avec une stratégie d'upsert (INSERT … ON CONFLICT DO UPDATE).

Configuration
-------------
  DATABASE_URL : variable d'environnement (défaut : postgresql://postgres:postgres@localhost:5432/urbandata)

Détection PostGIS
-----------------
  - Si PostGIS est installé (extension présente) : geometry_wkt → colonne GEOMETRY(Polygon, 4326)
  - Sinon : geometry_wkt conservée en TEXT (requêtable via ST_GeomFromText côté client)

Tables exportées
----------------
  gold_arrondissement_summary   — table maîtresse (PK : arrondissement)
  gold_indicator_scores         — vue allégée scores 0-100 (PK : arrondissement)
  gold_poi_catalog              — POI OSM (PK : id)
  gold_price_timeline           — série temporelle prix (PK : arrondissement, year)
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from src.ingestion.base import get_logger

load_dotenv(Path(__file__).parents[2] / ".env")

LOG_DIR = Path(__file__).parents[2] / "logs"
GOLD_ROOT = Path(__file__).parents[2] / "data" / "gold"

DEFAULT_DB_URL = "postgresql://postgres:postgres@localhost:5432/urbandata"

# ---------------------------------------------------------------------------
# Schémas SQL des tables Gold
# ---------------------------------------------------------------------------

_DDL_SUMMARY = """
CREATE TABLE IF NOT EXISTS gold_arrondissement_summary (
    arrondissement          SMALLINT    PRIMARY KEY,
    nom_arrondissement      TEXT,
    geometry_wkt            TEXT,
    -- Scores historiques
    anime_score             REAL,
    calme_score             REAL,
    -- Nouveaux scores stratégiques
    connectivity_score      REAL,
    mobility_score          REAL,
    health_env_score        REAL,
    tranquility_score       REAL,
    -- Score composite
    livability_score        REAL,
    -- Métriques brutes connectivité
    pct_eligible_ftth       REAL,
    pct_pop_4g_mean         REAL,
    pct_pop_5g_mean         REAL,
    pct_t2_t3               REAL,
    nb_t2                   INTEGER,
    nb_t3                   INTEGER,
    -- Métriques brutes mobilité
    station_count_velib     INTEGER,
    avg_bikes_available     REAL,
    avg_docks_available     REAL,
    avg_bikes_pct           REAL,
    electric_bike_ratio     REAL,
    -- Transports en commun (ICAR) par mode
    transit_stop_count      INTEGER,
    metro_count             INTEGER,
    rer_count               INTEGER,
    tram_count              INTEGER,
    bus_count               INTEGER,
    -- Métriques brutes santé environnementale
    nb_ilots_fraicheur      INTEGER,
    surface_fraicheur_ha    REAL,
    nb_arbres               INTEGER,
    arbres_per_km2          REAL,
    nb_airparif_stations    INTEGER,
    -- Qualité de l'air & pollen (Open-Meteo)
    european_aqi            REAL,
    pollen_total            REAL,
    pollen_risk             TEXT,
    -- Métriques brutes tranquillité
    crime_count_total       INTEGER,
    crime_rate_per_1000     REAL,
    noise_lden_surface_ha   REAL,
    noise_ln_surface_ha     REAL,
    nb_bars                 INTEGER,
    nb_nightclubs           INTEGER,
    -- Métriques historiques
    median_price            REAL,
    median_income           REAL,
    bar_count               INTEGER,
    park_count              INTEGER,
    -- Métriques animation / dynamisme (OSM)
    nightclub_count         INTEGER,
    cinema_count            INTEGER,
    restaurant_count        INTEGER,
    stadium_count           INTEGER,
    museum_count            INTEGER,
    -- Métadonnées
    updated_at              TIMESTAMPTZ
);
"""

_DDL_INDICATOR_SCORES = """
CREATE TABLE IF NOT EXISTS gold_indicator_scores (
    arrondissement          SMALLINT    PRIMARY KEY,
    nom_arrondissement      TEXT,
    geometry_wkt            TEXT,
    anime_score             REAL,
    calme_score             REAL,
    connectivity_score      REAL,
    mobility_score          REAL,
    health_env_score        REAL,
    tranquility_score       REAL,
    livability_score        REAL,
    updated_at              TIMESTAMPTZ
);
"""

_DDL_POI = """
CREATE TABLE IF NOT EXISTS gold_poi_catalog (
    id                      BIGINT      PRIMARY KEY,
    type                    TEXT,
    category                TEXT,
    name                    TEXT,
    lat                     REAL,
    lon                     REAL,
    hours                   TEXT,
    wheelchair_accessible   TEXT,
    updated_at              TIMESTAMPTZ
);
"""

_DDL_TIMELINE = """
CREATE TABLE IF NOT EXISTS gold_price_timeline (
    arrondissement          SMALLINT,
    year                    SMALLINT,
    median_price            REAL,
    transaction_count       INTEGER,
    PRIMARY KEY (arrondissement, year)
);
"""

_DDL_SOCIAL_HOUSING_TIMELINE = """
CREATE TABLE IF NOT EXISTS gold_social_housing_timeline (
    arrondissement          SMALLINT,
    annee                   SMALLINT,
    logements_finances      INTEGER,
    logements_cumules       INTEGER,
    PRIMARY KEY (arrondissement, annee)
);
"""

# Tables IRIS (grain primaire ~992 zones). code_iris est la PK (TEXT 9 chiffres).
# arrondissement reste exposé comme dimension parente.
_DDL_IRIS_SUMMARY = """
CREATE TABLE IF NOT EXISTS gold_iris_summary (
    code_iris               TEXT        PRIMARY KEY,
    arrondissement          SMALLINT,
    nom_iris                TEXT,
    geometry_wkt            TEXT,
    -- Scores (normalisés sur ~992 IRIS)
    anime_score             REAL,
    connectivity_score      REAL,
    mobility_score          REAL,
    health_env_score        REAL,
    tranquility_score       REAL,
    livability_score        REAL,
    -- Métriques brutes IRIS-natives (fortement discriminantes)
    median_price            REAL,
    median_income           REAL,
    gini_coefficient        REAL,
    poverty_rate            REAL,
    -- Comptages OSM par IRIS
    bar_count               INTEGER,
    nightclub_count         INTEGER,
    park_count              INTEGER,
    cinema_count            INTEGER,
    restaurant_count        INTEGER,
    stadium_count           INTEGER,
    museum_count            INTEGER,
    -- Mobilité
    station_count_velib     REAL,
    transit_stop_count      REAL,
    metro_count             REAL,
    -- Métadonnées
    updated_at              TIMESTAMPTZ
);
"""

_DDL_IRIS_INDICATOR_SCORES = """
CREATE TABLE IF NOT EXISTS gold_iris_indicator_scores (
    code_iris               TEXT        PRIMARY KEY,
    arrondissement          SMALLINT,
    nom_iris                TEXT,
    geometry_wkt            TEXT,
    anime_score             REAL,
    connectivity_score      REAL,
    mobility_score          REAL,
    health_env_score        REAL,
    tranquility_score       REAL,
    livability_score        REAL,
    median_price            REAL,
    median_income           REAL,
    gini_coefficient        REAL,
    poverty_rate            REAL,
    updated_at              TIMESTAMPTZ
);
"""

# Colonnes constituant la clé primaire de chaque table (pour l'upsert)
_PK_COLUMNS: dict[str, list[str]] = {
    "gold_arrondissement_summary": ["arrondissement"],
    "gold_indicator_scores":       ["arrondissement"],
    "gold_poi_catalog":            ["id"],
    "gold_price_timeline":         ["arrondissement", "year"],
    "gold_social_housing_timeline":["arrondissement", "annee"],
    "gold_iris_summary":           ["code_iris"],
    "gold_iris_indicator_scores":  ["code_iris"],
}

_DDL_MAP = {
    "gold_arrondissement_summary": _DDL_SUMMARY,
    "gold_indicator_scores":       _DDL_INDICATOR_SCORES,
    "gold_poi_catalog":            _DDL_POI,
    "gold_price_timeline":         _DDL_TIMELINE,
    "gold_social_housing_timeline":_DDL_SOCIAL_HOUSING_TIMELINE,
    "gold_iris_summary":           _DDL_IRIS_SUMMARY,
    "gold_iris_indicator_scores":  _DDL_IRIS_INDICATOR_SCORES,
}


# ---------------------------------------------------------------------------
# Connexion et détection PostGIS
# ---------------------------------------------------------------------------

def build_engine(db_url: str | None = None) -> Engine:
    url = db_url or os.environ.get("DATABASE_URL", DEFAULT_DB_URL)
    return create_engine(url, pool_pre_ping=True)


def _has_postgis(engine: Engine) -> bool:
    """Vérifie si l'extension PostGIS est activée."""
    try:
        with engine.connect() as conn:
            result = conn.execute(text(
                "SELECT COUNT(*) FROM pg_extension WHERE extname = 'postgis'"
            ))
            return result.scalar() > 0
    except Exception:
        return False


def _parse_ddl_columns(ddl: str) -> list[tuple[str, str]]:
    """Extrait les couples (nom, type) des colonnes déclarées dans un DDL CREATE TABLE."""
    cols: list[tuple[str, str]] = []
    for line in ddl.splitlines():
        s = line.strip().rstrip(",")
        if not s or s.startswith(("CREATE", ")", "--", "PRIMARY")):
            continue
        parts = s.split()
        if len(parts) >= 2 and parts[0].isidentifier():
            # type = jusqu'au mot-clé PRIMARY le cas échéant
            type_tokens = []
            for tok in parts[1:]:
                if tok.upper() == "PRIMARY":
                    break
                type_tokens.append(tok)
            cols.append((parts[0], " ".join(type_tokens)))
    return cols


def _ensure_schema(engine: Engine, table_name: str, logger: Any) -> None:
    """
    Crée la table si absente, puis ajoute idempotemment les colonnes manquantes.

    Le ALTER TABLE ... ADD COLUMN IF NOT EXISTS gère la migration des bases
    existantes lorsqu'on enrichit le schéma (ex. ajout european_aqi / pollen),
    sans devoir DROP la table.
    """
    ddl = _DDL_MAP.get(table_name)
    if not ddl:
        return
    with engine.begin() as conn:
        conn.execute(text(ddl))
        for col_name, col_type in _parse_ddl_columns(ddl):
            conn.execute(text(
                f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
            ))
    logger.debug("Table '%s' prête (schéma synchronisé)", table_name)


# ---------------------------------------------------------------------------
# Nettoyage du DataFrame avant insertion
# ---------------------------------------------------------------------------

def _clean_df_for_pg(df: pd.DataFrame, table_name: str, logger: Any) -> pd.DataFrame:
    """
    Aligne le DataFrame sur le DDL :
      - Supprime les colonnes inconnues (hors DDL)
      - Convertit les types pandas problématiques (Int64 → int, Timedelta, etc.)
      - Remplace les NaN par None (NULL PostgreSQL)
    """
    df = df.copy()

    # Convertir les nullable int Pandas en float (PostgreSQL accepte NULL float sans souci)
    for col in df.select_dtypes(include=["Int64", "Int32", "Int16", "Int8"]).columns:
        df[col] = df[col].astype("float64")

    # Convertir les Timestamp avec timezone vers str ISO si besoin
    for col in df.select_dtypes(include=["datetimetz", "datetime64[ns, UTC]"]).columns:
        df[col] = df[col].astype(str)

    # Remplacer NaN/NA/NaT par None (NULL PostgreSQL)
    # astype(object) brise les dtypes pandas spéciaux (Int64, float64…)
    # pour que where() et replace() opèrent sur des valeurs Python natives,
    # évitant psycopg2.errors.NumericValueOutOfRange sur les colonnes INTEGER.
    df = df.astype(object)
    df = df.where(pd.notnull(df), None)
    df = df.replace({np.nan: None, pd.NA: None})

    # Supprimer colonnes non attendues par le DDL (évite les erreurs d'insertion)
    ddl = _DDL_MAP.get(table_name, "")
    if ddl:
        declared_cols = {
            line.strip().split()[0].strip().lower()
            for line in ddl.splitlines()
            if line.strip() and not line.strip().startswith(("CREATE", ")", "--", "PRIMARY"))
               and len(line.strip().split()) >= 2
        }
        extra = [c for c in df.columns if c.lower() not in declared_cols]
        if extra:
            logger.debug("Colonnes ignorées (hors DDL) pour '%s' : %s", table_name, extra)
            df = df.drop(columns=extra, errors="ignore")

    return df


# ---------------------------------------------------------------------------
# Upsert générique
# ---------------------------------------------------------------------------

def upsert_table(
    engine: Engine,
    df: pd.DataFrame,
    table_name: str,
    logger: Any,
    chunk_size: int = 500,
) -> int:
    """
    Upsert (INSERT … ON CONFLICT DO UPDATE SET …) en mode chunked.

    Stratégie :
      1. INSERT INTO … VALUES (…)
      2. ON CONFLICT (pk_cols) DO UPDATE SET col = EXCLUDED.col, …

    Retourne le nombre de lignes traitées.
    """
    if df.empty:
        logger.warning("DataFrame vide pour '%s' — upsert ignoré", table_name)
        return 0

    pk_cols = _PK_COLUMNS.get(table_name, [])
    df = _clean_df_for_pg(df, table_name, logger)

    value_cols = [c for c in df.columns if c not in pk_cols]
    if not value_cols:
        logger.warning("Aucune colonne de valeur pour '%s'", table_name)
        return 0

    col_list   = ", ".join(df.columns)
    param_list = ", ".join(f":{c}" for c in df.columns)
    conflict   = ", ".join(pk_cols)
    update_set = ", ".join(f"{c} = EXCLUDED.{c}" for c in value_cols)

    sql = text(f"""
        INSERT INTO {table_name} ({col_list})
        VALUES ({param_list})
        ON CONFLICT ({conflict}) DO UPDATE SET {update_set}
    """)

    total = 0
    with engine.begin() as conn:
        for start in range(0, len(df), chunk_size):
            chunk = df.iloc[start : start + chunk_size]
            conn.execute(sql, chunk.to_dict("records"))
            total += len(chunk)
            logger.debug("  Upsert '%s' : %d/%d lignes", table_name, total, len(df))

    logger.info("Upsert '%s' terminé : %d lignes", table_name, total)
    return total


# ---------------------------------------------------------------------------
# Chargement des tables Gold depuis Parquet
# ---------------------------------------------------------------------------

def _load_gold(filename: str, logger: Any) -> pd.DataFrame:
    path = GOLD_ROOT / filename
    if not path.exists():
        logger.warning("Gold Parquet introuvable : %s", path)
        return pd.DataFrame()
    try:
        return pd.read_parquet(path, engine="pyarrow")
    except Exception as exc:
        logger.error("Lecture Gold '%s' : %s", filename, exc)
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Point d'entrée principal
# ---------------------------------------------------------------------------

def export_all(
    db_url: str | None = None,
    dry_run: bool = False,
    tables: list[str] | None = None,
) -> dict[str, int]:
    """
    Exporte les tables Gold vers PostgreSQL.

    Paramètres
    ----------
    db_url   : URL de connexion SQLAlchemy (priorité sur DATABASE_URL)
    dry_run  : si True, vérifie la connexion et affiche les stats sans écrire
    tables   : sous-ensemble de tables à exporter (défaut : toutes)

    Retourne
    --------
    dict {table_name: rows_upserted}
    """
    logger = get_logger("gold_export_pg", LOG_DIR)

    engine = build_engine(db_url)
    logger.info("Connexion PostgreSQL : %s", engine.url)

    # Test de connexion
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("Connexion PostgreSQL OK")
    except Exception as exc:
        logger.error("Connexion PostgreSQL échouée : %s", exc)
        raise

    postgis_ok = _has_postgis(engine)
    logger.info("PostGIS disponible : %s", postgis_ok)

    if dry_run:
        logger.info("Mode dry-run — aucune écriture en base")
        for filename in ["arrondissement_summary.parquet", "indicator_scores.parquet"]:
            df = _load_gold(filename, logger)
            logger.info("  %s → %d lignes disponibles", filename, len(df))
        return {}

    # Matrice fichier → table
    export_plan = [
        ("arrondissement_summary.parquet",     "gold_arrondissement_summary"),
        ("indicator_scores.parquet",           "gold_indicator_scores"),
        ("poi_catalog.parquet",                "gold_poi_catalog"),
        ("price_timeline.parquet",             "gold_price_timeline"),
        ("social_housing_timeline.parquet",    "gold_social_housing_timeline"),
        ("iris_summary.parquet",               "gold_iris_summary"),
        ("iris_indicator_scores.parquet",      "gold_iris_indicator_scores"),
    ]

    if tables:
        export_plan = [(f, t) for f, t in export_plan if t in tables or
                       any(alias in t for alias in tables)]

    results: dict[str, int] = {}
    for filename, table_name in export_plan:
        logger.info("--- Export '%s' ---", table_name)
        try:
            _ensure_schema(engine, table_name, logger)
            df = _load_gold(filename, logger)
            if df.empty:
                logger.warning("  Parquet vide — table non mise à jour")
                results[table_name] = 0
                continue
            n = upsert_table(engine, df, table_name, logger)
            results[table_name] = n
        except Exception as exc:
            logger.error("Erreur export '%s' : %s", table_name, exc, exc_info=True)
            results[table_name] = -1

    logger.info("Export PostgreSQL terminé : %s", results)
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Export des tables Gold vers PostgreSQL",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  # Export complet
  python -m src.gold.export_pg

  # Vérifier la connexion sans écrire
  python -m src.gold.export_pg --dry-run

  # Exporter uniquement la table maîtresse
  python -m src.gold.export_pg --tables gold_arrondissement_summary

  # URL de connexion personnalisée
  DATABASE_URL=postgresql://user:pass@host:5432/db python -m src.gold.export_pg
""",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Vérifie la connexion sans écrire en base")
    parser.add_argument("--tables", nargs="*", metavar="TABLE",
                        help="Tables à exporter (défaut : toutes)")
    parser.add_argument("--db-url", metavar="URL",
                        help="URL SQLAlchemy (surcharge DATABASE_URL)")
    args = parser.parse_args()

    try:
        results = export_all(
            db_url=args.db_url,
            dry_run=args.dry_run,
            tables=args.tables,
        )
        for table, count in results.items():
            status = "OK " if count >= 0 else "ERR"
            print(f"  [{status}]  {table} : {count} lignes")
        sys.exit(0 if all(v >= 0 for v in results.values()) else 1)
    except Exception as e:
        print(f"ERREUR : {e}", file=sys.stderr)
        sys.exit(1)
