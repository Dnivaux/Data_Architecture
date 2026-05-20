"""
Gold Layer Builder
==================
Construit les tables finales analytiques prêtes pour l'API et PostgreSQL.

Tables produites (data/gold/) :
  arrondissement_summary.parquet   — table maîtresse (clé : arrondissement)
  poi_catalog.parquet              — catalogue POI enrichi (clé : osm_id)
  price_timeline.parquet           — série temporelle prix DVF
  indicator_scores.parquet         — 4 scores stratégiques + géométrie WKT

Schéma arrondissement_summary (clé primaire pour PostgreSQL)
------------------------------------------------------------
  arrondissement          int   PK 1-20
  nom_arrondissement      str   "Paris 1er", "Paris 2e", ...
  geometry_wkt            str   Polygone WKT EPSG:4326 (PostGIS-ready)
  --- Scores historiques ---
  anime_score             float 0-100
  calme_score             float 0-100
  accessibilite_score     float 0-100
  --- Nouveaux scores stratégiques ---
  connectivity_score      float 0-100
  mobility_score          float 0-100
  health_env_score        float 0-100
  tranquility_score       float 0-100
  --- Score composite ---
  livability_score        float 0-100 (moyenne pondérée de tous les scores)
  --- Métriques brutes clés ---
  pct_eligible_ftth       float  % locaux éligibles fibre
  pct_pop_4g_mean         float  % population couverte 4G
  pct_t2_t3               float  % logements T2/T3
  station_count_velib     int   Nb stations Vélib'
  avg_bikes_available     float  Dispo moy. vélos
  nb_ilots_fraicheur      int   Nb îlots de fraîcheur
  surface_fraicheur_ha    float  Surface espaces verts (ha)
  arbres_per_km2          float  Densité arborée
  crime_count_total       int   Délits (dernière année)
  crime_rate_per_1000     float  Taux pour 1000 hab.
  noise_lden_surface_ha   float  Surface exposée bruit Lden (ha)
  nb_bars                 int
  nb_nightclubs           int
  median_price            float  Prix médian DVF (€)
  bar_count               int   (OSM legacy)
  park_count              int
  --- Métadonnées ---
  updated_at              datetime
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.ingestion.base import get_logger, read_parquet, save_parquet

LOG_DIR = Path(__file__).parents[2] / "logs"
SILVER_ROOT = Path(__file__).parents[2] / "data" / "silver"
GOLD_ROOT   = Path(__file__).parents[2] / "data" / "gold"

PARIS_ARRONDISSEMENTS = list(range(1, 21))

_NOMS_ARR = {
    1: "Paris 1er",  2: "Paris 2e",   3: "Paris 3e",   4: "Paris 4e",
    5: "Paris 5e",   6: "Paris 6e",   7: "Paris 7e",   8: "Paris 8e",
    9: "Paris 9e",  10: "Paris 10e", 11: "Paris 11e", 12: "Paris 12e",
    13: "Paris 13e", 14: "Paris 14e", 15: "Paris 15e", 16: "Paris 16e",
    17: "Paris 17e", 18: "Paris 18e", 19: "Paris 19e", 20: "Paris 20e",
}

# Poids du score composite de vivabilité globale
_LIVABILITY_WEIGHTS = {
    "anime_score":        0.10,
    "calme_score":        0.15,
    "accessibilite_score": 0.15,
    "connectivity_score": 0.15,
    "mobility_score":     0.15,
    "health_env_score":   0.15,
    "tranquility_score":  0.15,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_silver(filename: str, logger: logging.Logger) -> pd.DataFrame:
    path = SILVER_ROOT / filename
    if not path.exists():
        logger.warning("Silver introuvable : %s", filename)
        return pd.DataFrame()
    try:
        return pd.read_parquet(path, engine="pyarrow")
    except Exception as exc:
        logger.error("Lecture Silver '%s' : %s", filename, exc)
        return pd.DataFrame()


def _save_gold(df: pd.DataFrame, filename: str, logger: logging.Logger) -> Path:
    GOLD_ROOT.mkdir(parents=True, exist_ok=True)
    path = GOLD_ROOT / filename
    df.to_parquet(path, index=False, engine="pyarrow", compression="snappy")
    logger.info("Gold → %d lignes : %s", len(df), path)
    return path


def _load_boundaries_wkt(logger: logging.Logger) -> pd.DataFrame:
    """Charge les polygones boundaries pour enrichir la Gold avec géométrie WKT."""
    df = read_parquet("boundaries")
    if df.empty:
        logger.warning("Boundaries Bronze vides — geometry_wkt absente en Gold")
        return pd.DataFrame({"arrondissement": PARIS_ARRONDISSEMENTS, "geometry_wkt": None})

    geom_col = "geometry_wkt" if "geometry_wkt" in df.columns else "geometry"
    if "arrondissement" not in df.columns:
        candidates = [c for c in df.columns if "arr" in c.lower()]
        if candidates:
            df = df.rename(columns={candidates[0]: "arrondissement"})

    return df[["arrondissement", geom_col]].rename(columns={geom_col: "geometry_wkt"})


def _compute_livability(df: pd.DataFrame) -> pd.Series:
    """Calcule le score composite pondéré. Les NaN sont remplacés par 50 (neutre)."""
    total_weight = sum(_LIVABILITY_WEIGHTS.values())
    score = pd.Series(0.0, index=df.index)
    for col, weight in _LIVABILITY_WEIGHTS.items():
        if col in df.columns:
            score += df[col].fillna(50.0) * weight
        else:
            score += 50.0 * weight
    return (score / total_weight).round(1)


# ---------------------------------------------------------------------------
# Table 1 — Arrondissement Summary (table maîtresse Gold)
# ---------------------------------------------------------------------------

def build_arrondissement_summary(logger: logging.Logger) -> pd.DataFrame:
    """
    Fusionne tous les Silver pour produire une table analytique complète
    par arrondissement, avec géométrie WKT et tous les scores.
    """
    base = pd.DataFrame({
        "arrondissement":   PARIS_ARRONDISSEMENTS,
        "nom_arrondissement": [_NOMS_ARR[i] for i in PARIS_ARRONDISSEMENTS],
    })

    # Géométrie
    boundaries = _load_boundaries_wkt(logger)
    base = base.merge(boundaries, on="arrondissement", how="left")

    # Scores complets (Silver scores_by_arrondissement)
    scores = _read_silver("scores_by_arrondissement.parquet", logger)
    if not scores.empty:
        # Supprimer les colonnes de métadonnées avant merge
        drop_cols = [c for c in ["geometry", "geometry_wkt", "computed_at", "ingested_at"]
                     if c in scores.columns]
        base = base.merge(scores.drop(columns=drop_cols, errors="ignore"),
                          on="arrondissement", how="left")

    # Métriques brutes Silver indicateurs
    for filename, cols in [
        ("connectivity_by_arrondissement.parquet",
         ["pct_eligible_ftth", "pct_pop_4g_mean", "pct_pop_5g_mean", "pct_t2_t3",
          "nb_t2", "nb_t3"]),
        ("mobility_by_arrondissement.parquet",
         ["station_count_velib", "avg_bikes_available", "avg_docks_available",
          "avg_bikes_pct", "electric_bike_ratio"]),
        ("health_env_by_arrondissement.parquet",
         ["nb_ilots_fraicheur", "surface_fraicheur_ha", "nb_arbres",
          "arbres_per_km2", "nb_airparif_stations"]),
        ("tranquility_by_arrondissement.parquet",
         ["crime_count_total", "crime_rate_per_1000",
          "noise_lden_surface_ha", "noise_ln_surface_ha",
          "nb_bars", "nb_nightclubs"]),
    ]:
        df_s = _read_silver(filename, logger)
        if not df_s.empty:
            available = [c for c in cols if c in df_s.columns]
            existing  = [c for c in available if c not in base.columns]
            if existing:
                base = base.merge(
                    df_s[["arrondissement"] + existing],
                    on="arrondissement", how="left",
                )

    # Prix DVF (dernière année)
    prices = _read_silver("prices_by_arrondissement_year.parquet", logger)
    if not prices.empty and "year" in prices.columns:
        latest = prices[prices["year"] == prices["year"].max()]
        if "median_price" not in base.columns:
            base = base.merge(
                latest[["arrondissement", "median_price"]],
                on="arrondissement", how="left",
            )

    # Amenities OSM (bar_count, park_count legacy)
    amenities = _read_silver("amenities_by_arrondissement.parquet", logger)
    if not amenities.empty:
        for col in ["bar_count", "park_count"]:
            if col not in base.columns and col in amenities.columns:
                base = base.merge(
                    amenities[["arrondissement", col]],
                    on="arrondissement", how="left",
                )

    # Score composite de vivabilité globale
    base["livability_score"] = _compute_livability(base)

    base["updated_at"] = datetime.now(timezone.utc)
    return base.sort_values("arrondissement").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Table 2 — Indicator Scores (vue analytique allégée pour graphiques)
# ---------------------------------------------------------------------------

def build_indicator_scores(logger: logging.Logger) -> pd.DataFrame:
    """
    Table Gold allégée pour tableaux de bord :
    arrondissement + nom + 7 scores + livability_score + geometry_wkt.
    Idéale pour les visualisations choroplèthes.
    """
    summary = _read_silver("scores_by_arrondissement.parquet", logger)
    base = pd.DataFrame({
        "arrondissement":     PARIS_ARRONDISSEMENTS,
        "nom_arrondissement": [_NOMS_ARR[i] for i in PARIS_ARRONDISSEMENTS],
    })

    boundaries = _load_boundaries_wkt(logger)
    base = base.merge(boundaries, on="arrondissement", how="left")

    if not summary.empty:
        score_cols = [c for c in summary.columns
                      if c.endswith("_score") and c in summary.columns]
        drop_geom = [c for c in ["geometry", "geometry_wkt", "computed_at"] if c in summary.columns]
        base = base.merge(
            summary[["arrondissement"] + score_cols].drop(columns=drop_geom, errors="ignore"),
            on="arrondissement", how="left",
        )

    base["livability_score"] = _compute_livability(base)
    base["updated_at"] = datetime.now(timezone.utc)
    return base.sort_values("arrondissement").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Table 3 — POI Catalog (inchangé, enrichi avec arrondissement)
# ---------------------------------------------------------------------------

def build_poi_catalog(logger: logging.Logger) -> pd.DataFrame:
    osm_df = read_parquet("osm")
    if osm_df.empty:
        return pd.DataFrame()

    poi_df = osm_df[[
        "osm_id", "osm_type", "amenity_type", "name",
        "latitude", "longitude", "opening_hours", "wheelchair",
    ]].copy()
    poi_df.columns = [
        "id", "type", "category", "name",
        "lat", "lon", "hours", "wheelchair_accessible",
    ]
    poi_df["updated_at"] = datetime.now(timezone.utc)
    return poi_df


# ---------------------------------------------------------------------------
# Table 4 — Price Timeline (inchangé)
# ---------------------------------------------------------------------------

def build_price_timeline(logger: logging.Logger) -> pd.DataFrame:
    path = SILVER_ROOT / "prices_by_arrondissement_year.parquet"
    if not path.exists():
        logger.warning("prices_by_arrondissement_year.parquet absent")
        return pd.DataFrame()
    try:
        df = pd.read_parquet(path, engine="pyarrow")
        return df[["arrondissement", "year", "median_price", "transaction_count"]].copy()
    except Exception as exc:
        logger.error("Lecture price_timeline : %s", exc)
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Orchestrateur principal
# ---------------------------------------------------------------------------

def build_gold_layer() -> None:
    """Construit toutes les tables Gold et les persiste en Parquet."""
    logger = get_logger("gold_builder", LOG_DIR)
    logger.info("=" * 60)
    logger.info("Gold layer — démarrage")
    logger.info("=" * 60)

    GOLD_ROOT.mkdir(parents=True, exist_ok=True)

    # Table maîtresse (toutes métriques + géométrie)
    logger.info(">>> Table 1/4 : arrondissement_summary")
    summary = build_arrondissement_summary(logger)
    _save_gold(summary, "arrondissement_summary.parquet", logger)

    # Vue scores allégée (pour choroplèthes)
    logger.info(">>> Table 2/4 : indicator_scores")
    scores = build_indicator_scores(logger)
    _save_gold(scores, "indicator_scores.parquet", logger)

    # Catalogue POI
    logger.info(">>> Table 3/4 : poi_catalog")
    poi = build_poi_catalog(logger)
    if not poi.empty:
        _save_gold(poi, "poi_catalog.parquet", logger)

    # Série temporelle prix
    logger.info(">>> Table 4/4 : price_timeline")
    timeline = build_price_timeline(logger)
    if not timeline.empty:
        _save_gold(timeline, "price_timeline.parquet", logger)

    logger.info("=" * 60)
    logger.info("Gold layer complet — %d tables", 4)
    logger.info("=" * 60)
