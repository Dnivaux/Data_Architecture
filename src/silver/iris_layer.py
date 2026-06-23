"""
Silver Layer — Maille IRIS (grain primaire)
============================================
Construit les tables Silver à la maille IRIS (~992 zones parisiennes), avec
l'arrondissement conservé comme dimension parente.

Deux stratégies selon la disponibilité réelle de la source :

  • IRIS natif (jointure spatiale ou clé IRIS) — variation *à l'intérieur* d'un
    arrondissement :
      - prix DVF (points géocodés → sjoin IRIS)
      - revenus INSEE FiLoSoFi (déjà au code_iris)
      - aménités OSM (points → sjoin IRIS) : bars, restaurants, parcs…
      - mobilité Vélib' + ICAR (points → sjoin IRIS)

  • Rediffusion arrondissement → IRIS (`granularite='arrondissement'`) pour les
    sources publiées uniquement à l'arrondissement/commune :
      - connectivité (ARCEP commune)
      - qualité de l'air (Open-Meteo arrondissement)
      - îlots de fraîcheur / canopée (Paris OD arrondissement)
      - criminalité (SSMSI) + bruit (Bruitparif) — composantes de tranquillité

Sorties (data/silver/) :
  iris_base.parquet                 — squelette 992 IRIS + géométrie + surface
  amenities_by_iris.parquet         — comptages OSM par IRIS
  prices_by_iris_year.parquet       — prix DVF par IRIS × année
  revenus_by_iris.parquet           — revenus INSEE par IRIS
  mobility_by_iris.parquet          — Vélib' + ICAR par IRIS
  connectivity_by_iris.parquet      — rediffusion arrondissement
  health_env_by_iris.parquet        — rediffusion arrondissement
  tranquility_by_iris.parquet       — crime/bruit rediffusés + bars/clubs IRIS
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd

from src.ingestion.base import get_logger, read_parquet
from .indicators import _load_iris_gdf, _sjoin_points_to_iris

LOG_DIR = Path(__file__).parents[2] / "logs"
SILVER_ROOT = Path(__file__).parents[2] / "data" / "silver"

# CRS projeté métrique pour Paris (Lambert-93) — calcul des surfaces.
_LAMBERT93 = "EPSG:2154"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _save(df: pd.DataFrame, filename: str, logger: logging.Logger) -> None:
    SILVER_ROOT.mkdir(parents=True, exist_ok=True)
    path = SILVER_ROOT / filename
    df.to_parquet(path, index=False, engine="pyarrow", compression="snappy")
    logger.info("  Silver IRIS → %s (%d lignes)", filename, len(df))


def build_iris_base(iris_gdf: gpd.GeoDataFrame, logger: logging.Logger) -> pd.DataFrame:
    """Squelette des 992 IRIS : code_iris, arrondissement, nom_iris, surface_km2.

    La surface est calculée par reprojection en Lambert-93 (mètres), ce qui
    fournit les densités IRIS (arbres/km², etc.) sans surface fournie par l'API.
    """
    if iris_gdf is None or iris_gdf.empty:
        logger.error("IRIS gdf vide — iris_base non construite")
        return pd.DataFrame(columns=["code_iris", "arrondissement", "nom_iris", "surface_km2"])

    base = iris_gdf.copy()
    try:
        base["surface_km2"] = base.geometry.to_crs(_LAMBERT93).area / 1_000_000
    except Exception as exc:
        logger.warning("Calcul surface IRIS échoué (%s) — surface_km2 à NA", exc)
        base["surface_km2"] = pd.NA

    out = pd.DataFrame({
        "code_iris": base["code_iris"].astype(str),
        "arrondissement": pd.to_numeric(base["arrondissement"], errors="coerce").astype("Int64"),
        "nom_iris": base.get("nom_iris"),
        "surface_km2": pd.to_numeric(base["surface_km2"], errors="coerce").round(4),
    })
    return out.sort_values(["arrondissement", "code_iris"]).reset_index(drop=True)


def _broadcast_to_iris(
    df_arr: pd.DataFrame,
    iris_base: pd.DataFrame,
    value_cols: list[str],
    logger: logging.Logger,
) -> pd.DataFrame:
    """Rediffuse les valeurs arrondissement vers chaque IRIS enfant.

    Merge sur `arrondissement`, ajoute `granularite='arrondissement'` aux lignes
    effectivement rediffusées. Les IRIS sans donnée restent NA.
    """
    cols = [c for c in value_cols if c in df_arr.columns]
    if df_arr.empty or not cols:
        return iris_base[["code_iris", "arrondissement"]].copy()

    src = df_arr[["arrondissement"] + cols].copy()
    src["arrondissement"] = pd.to_numeric(src["arrondissement"], errors="coerce").astype("Int64")
    merged = iris_base[["code_iris", "arrondissement"]].merge(
        src, on="arrondissement", how="left"
    )
    merged["granularite"] = "arrondissement"
    return merged


# ---------------------------------------------------------------------------
# IRIS natif
# ---------------------------------------------------------------------------

def build_amenities_by_iris(iris_gdf: gpd.GeoDataFrame, logger: logging.Logger) -> pd.DataFrame:
    """Comptage des POI OSM par IRIS (sjoin point→polygone).

    Produit une colonne par type d'aménité utile au scoring d'animation :
    bar, nightclub, park, cinema, restaurant, stadium.
    """
    osm = read_parquet("osm")
    if osm.empty or {"latitude", "longitude", "amenity_type"} - set(osm.columns):
        logger.warning("Bronze osm vide/mal formé — amenities_by_iris vide")
        return pd.DataFrame()

    joined = _sjoin_points_to_iris(osm, "latitude", "longitude", iris_gdf, logger)
    joined = joined.dropna(subset=["code_iris"])

    counts = (
        joined.groupby(["code_iris", "amenity_type"]).size()
        .unstack(fill_value=0).reset_index()
    )
    for col in ["bar", "nightclub", "park", "cinema", "restaurant", "stadium"]:
        if col not in counts.columns:
            counts[col] = 0
    counts = counts.rename(columns={
        "bar": "bar_count", "nightclub": "nightclub_count", "park": "park_count",
        "cinema": "cinema_count", "restaurant": "restaurant_count", "stadium": "stadium_count",
    })
    keep = ["code_iris", "bar_count", "nightclub_count", "park_count",
            "cinema_count", "restaurant_count", "stadium_count"]
    counts["granularite"] = "iris"
    return counts[keep + ["granularite"]]


def build_prices_by_iris(iris_gdf: gpd.GeoDataFrame, logger: logging.Logger) -> pd.DataFrame:
    """Prix DVF au m² agrégés par IRIS × année (sjoin point→IRIS)."""
    dvf = read_parquet("dvf_clean")
    if dvf.empty or {"latitude", "longitude"} - set(dvf.columns):
        logger.warning("Bronze dvf_clean vide/sans coordonnées — prices_by_iris vide")
        return pd.DataFrame()

    dvf = dvf.copy()
    dvf["year"] = pd.to_datetime(dvf["date_mutation"], errors="coerce").dt.year
    dvf = dvf[
        (dvf["surface_reelle_bati"] > 0)
        & (dvf["valeur_fonciere"] > 0)
        & dvf["year"].notna()
    ].copy()
    if dvf.empty:
        return pd.DataFrame()
    dvf["prix_m2"] = dvf["valeur_fonciere"] / dvf["surface_reelle_bati"]

    joined = _sjoin_points_to_iris(dvf, "latitude", "longitude", iris_gdf, logger)
    joined = joined.dropna(subset=["code_iris"])

    grouped = (
        joined.groupby(["code_iris", "year"])
        .agg(
            transaction_count=("prix_m2", "count"),
            mean_price=("prix_m2", "mean"),
            median_price=("prix_m2", "median"),
        )
        .reset_index()
    )
    grouped["year"] = grouped["year"].astype(int)
    grouped["mean_price"] = grouped["mean_price"].round(0)
    grouped["median_price"] = grouped["median_price"].round(0)
    grouped["granularite"] = "iris"
    return grouped


def build_revenus_by_iris(logger: logging.Logger) -> pd.DataFrame:
    """Revenus INSEE FiLoSoFi par IRIS (déjà au grain IRIS dans le Bronze)."""
    rev = read_parquet("revenus")
    if rev.empty or "iris_code" not in rev.columns:
        logger.warning("Bronze revenus vide/sans iris_code — revenus_by_iris vide")
        return pd.DataFrame()

    rev = rev.copy()
    # Dernier millésime ingéré si plusieurs (partitions date=)
    if "year" in rev.columns and rev["year"].notna().any():
        rev = rev[rev["year"] == rev["year"].max()]
    rev = rev.dropna(subset=["iris_code"]).drop_duplicates(subset=["iris_code"], keep="last")

    out = pd.DataFrame({
        "code_iris": rev["iris_code"].astype(str),
        "arrondissement": pd.to_numeric(rev.get("arrondissement"), errors="coerce").astype("Int64"),
        "median_income": pd.to_numeric(rev.get("median_income"), errors="coerce"),
        "gini_coefficient": pd.to_numeric(rev.get("gini_coefficient"), errors="coerce"),
        "poverty_rate": pd.to_numeric(rev.get("poverty_rate"), errors="coerce"),
    })
    out["granularite"] = "iris"
    return out


def build_mobility_by_iris(iris_gdf: gpd.GeoDataFrame, logger: logging.Logger) -> pd.DataFrame:
    """Mobilité par IRIS : densité stations Vélib' + comptage arrêts ICAR par mode."""
    base = pd.DataFrame({"code_iris": iris_gdf["code_iris"].astype(str)}) if not iris_gdf.empty else pd.DataFrame()

    # --- Vélib' (points → IRIS) ---
    vel = read_parquet("velib")
    if not vel.empty:
        vel = vel.loc[:, ~vel.columns.duplicated()].rename(
            columns={"lat": "latitude", "lon": "longitude"}, errors="ignore"
        )
        if {"latitude", "longitude"}.issubset(vel.columns):
            vj = _sjoin_points_to_iris(vel, "latitude", "longitude", iris_gdf, logger)
            vj = vj.dropna(subset=["code_iris"])
            col_code = next((c for c in ["station_code", "stationCode"] if c in vj.columns), None)
            col_bikes = next((c for c in ["bikes_available", "numBikesAvailable"] if c in vj.columns), None)
            agg = {}
            if col_code:
                agg["station_count_velib"] = (col_code, "nunique")
            if col_bikes:
                agg["avg_bikes_available"] = (col_bikes, "mean")
            if agg:
                vel_agg = vj.groupby("code_iris").agg(**agg).reset_index()
                if "avg_bikes_available" in vel_agg.columns:
                    vel_agg["avg_bikes_available"] = pd.to_numeric(
                        vel_agg["avg_bikes_available"], errors="coerce"
                    ).round(2)
                base = base.merge(vel_agg, on="code_iris", how="left")

    # --- ICAR transit (points → IRIS) ---
    icar = read_parquet("icar")
    if not icar.empty and {"latitude", "longitude", "transport_mode"}.issubset(icar.columns):
        if "batch_ts" in icar.columns:
            icar = icar[icar["batch_ts"] == icar["batch_ts"].max()].copy()
        icar = icar.dropna(subset=["latitude", "longitude"])
        ij = _sjoin_points_to_iris(icar, "latitude", "longitude", iris_gdf, logger)
        ij = ij.dropna(subset=["code_iris"])
        if not ij.empty:
            total = ij.groupby("code_iris").size().reset_index(name="transit_stop_count")
            modes = (
                ij.groupby(["code_iris", "transport_mode"]).size()
                .unstack(fill_value=0).reset_index()
            )
            for mode in ["metro", "rer", "tram", "bus"]:
                if mode not in modes.columns:
                    modes[mode] = 0
            modes = modes.rename(columns={
                "metro": "metro_count", "rer": "rer_count",
                "tram": "tram_count", "bus": "bus_count",
            })[["code_iris", "metro_count", "rer_count", "tram_count", "bus_count"]]
            base = base.merge(total, on="code_iris", how="left")
            base = base.merge(modes, on="code_iris", how="left")

    if base.empty:
        return base
    # Comptages manquants → 0 (un IRIS sans station/arrêt = densité nulle, pas NA)
    for col in ["station_count_velib", "transit_stop_count",
                "metro_count", "rer_count", "tram_count", "bus_count"]:
        if col in base.columns:
            base[col] = pd.to_numeric(base[col], errors="coerce").fillna(0)
    base["granularite"] = "iris"
    return base


# ---------------------------------------------------------------------------
# Orchestrateur
# ---------------------------------------------------------------------------

def _read_silver(filename: str) -> pd.DataFrame:
    path = SILVER_ROOT / filename
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(path, engine="pyarrow")
    except Exception:
        return pd.DataFrame()


def build_iris_silver_layer(logger: logging.Logger | None = None) -> pd.DataFrame:
    """Construit toutes les tables Silver IRIS. Retourne la table iris_base.

    À exécuter APRÈS la couche Silver arrondissement (les tables arrondissement
    servent de source à la rediffusion connectivité/santé/tranquillité).
    """
    log = logger or get_logger("silver_iris", LOG_DIR)
    started = datetime.now(timezone.utc)
    log.info("=" * 60)
    log.info("Silver IRIS — démarrage")
    log.info("=" * 60)

    iris_gdf = _load_iris_gdf(log)
    if iris_gdf.empty:
        log.error("IRIS indisponibles — couche Silver IRIS annulée")
        return pd.DataFrame()

    iris_base = build_iris_base(iris_gdf, log)
    _save(iris_base, "iris_base.parquet", log)

    # --- IRIS natif ---
    log.info(">>> Agrégations IRIS natives (sjoin / clé IRIS)")
    amenities = build_amenities_by_iris(iris_gdf, log)
    if not amenities.empty:
        _save(amenities, "amenities_by_iris.parquet", log)

    prices = build_prices_by_iris(iris_gdf, log)
    if not prices.empty:
        _save(prices, "prices_by_iris_year.parquet", log)

    revenus = build_revenus_by_iris(log)
    if not revenus.empty:
        _save(revenus, "revenus_by_iris.parquet", log)

    mobility = build_mobility_by_iris(iris_gdf, log)
    if not mobility.empty:
        _save(mobility, "mobility_by_iris.parquet", log)

    # --- Rediffusion arrondissement → IRIS ---
    log.info(">>> Rediffusion arrondissement → IRIS (sources non infra-communales)")

    conn_arr = _read_silver("connectivity_by_arrondissement.parquet")
    conn_iris = _broadcast_to_iris(
        conn_arr, iris_base,
        ["pct_pop_4g_mean", "pct_pop_5g_mean", "pct_eligible_ftth",
         "nb_t2", "nb_t3", "pct_t2_t3"],
        log,
    )
    _save(conn_iris, "connectivity_by_iris.parquet", log)

    health_arr = _read_silver("health_env_by_arrondissement.parquet")
    health_iris = _broadcast_to_iris(
        health_arr, iris_base,
        ["european_aqi", "avg_atmo_index", "pollen_total", "pollen_risk",
         "nb_ilots_fraicheur", "surface_fraicheur_ha", "nb_arbres", "arbres_per_km2"],
        log,
    )
    _save(health_iris, "health_env_by_iris.parquet", log)

    # Tranquillité : crime + bruit rediffusés ; bars/clubs natifs IRIS (OSM)
    tranq_arr = _read_silver("tranquility_by_arrondissement.parquet")
    tranq_iris = _broadcast_to_iris(
        tranq_arr, iris_base,
        ["crime_count_total", "crime_rate_per_1000",
         "noise_lden_surface_ha", "noise_ln_surface_ha"],
        log,
    )
    if not amenities.empty:
        tranq_iris = tranq_iris.merge(
            amenities.rename(columns={"bar_count": "nb_bars",
                                      "nightclub_count": "nb_nightclubs"})
            [["code_iris", "nb_bars", "nb_nightclubs"]],
            on="code_iris", how="left",
        )
    _save(tranq_iris, "tranquility_by_iris.parquet", log)

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    log.info("=" * 60)
    log.info("Silver IRIS complet — %d IRIS (%.1fs)", len(iris_base), elapsed)
    log.info("=" * 60)
    return iris_base
