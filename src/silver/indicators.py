"""
Silver Layer — Agrégations spatiales par arrondissement
========================================================
Ce module produit une table Silver par indicateur stratégique,
en liant chaque point de donnée Bronze à son arrondissement via :
  - Cas 1 : commune_code (code INSEE) → arrondissement par lookup direct
  - Cas 2 : lat/lon → gpd.sjoin(predicate="within") sur les polygones boundaries
  - Cas 3 : label string Paris OD → mapping dict vers int

Sorties (data/silver/) :
  connectivity_by_arrondissement.parquet
  mobility_by_arrondissement.parquet
  health_env_by_arrondissement.parquet
  tranquility_by_arrondissement.parquet
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely import wkt

from src.ingestion.base import get_logger, read_parquet

LOG_DIR = Path(__file__).parents[2] / "logs"
SILVER_ROOT = Path(__file__).parents[2] / "data" / "silver"

# Surface approximative des arrondissements de Paris en km² (source : INSEE)
# Utilisée pour calculer des densités quand les boundaries ne contiennent pas l'aire.
_ARR_AREA_KM2: dict[int, float] = {
    1: 1.83,  2: 0.99,  3: 1.17,  4: 1.60,  5: 2.54,
    6: 2.15,  7: 4.09,  8: 3.88,  9: 2.18, 10: 2.89,
    11: 3.67, 12: 16.32, 13: 7.15, 14: 5.64, 15: 8.50,
    16: 16.31, 17: 5.67, 18: 6.01, 19: 6.79, 20: 5.98,
}

PARIS_ARRONDISSEMENTS = list(range(1, 21))


# ---------------------------------------------------------------------------
# Helpers partagés
# ---------------------------------------------------------------------------

def _load_boundaries_gdf(logger: logging.Logger) -> gpd.GeoDataFrame:
    """Charge les polygones d'arrondissement depuis le Bronze boundaries."""
    df = read_parquet("boundaries")
    if df.empty:
        logger.error("Bronze boundaries vide — exécuter ingest_boundaries() d'abord")
        return gpd.GeoDataFrame()

    geom_col = "geometry_wkt" if "geometry_wkt" in df.columns else "geometry"
    try:
        geom = gpd.GeoSeries.from_wkt(df[geom_col])
    except Exception as exc:
        logger.error("Impossible de parser la géométrie boundaries : %s", exc)
        return gpd.GeoDataFrame()

    gdf = gpd.GeoDataFrame(df, geometry=geom, crs="EPSG:4326")
    # Normaliser le nom de la colonne arrondissement
    if "arrondissement" not in gdf.columns:
        candidates = [c for c in gdf.columns if "arr" in c.lower()]
        if candidates:
            gdf = gdf.rename(columns={candidates[0]: "arrondissement"})
    logger.info("Boundaries chargées : %d polygones", len(gdf))
    return gdf[["arrondissement", "geometry"]].copy()


def _sjoin_to_arrondissement(
    df: pd.DataFrame,
    lat_col: str,
    lon_col: str,
    boundaries_gdf: gpd.GeoDataFrame,
    logger: logging.Logger,
) -> pd.DataFrame:
    """
    Cas 2 — jointure spatiale point-dans-polygone.
    Retourne df enrichi d'une colonne 'arrondissement'.
    """
    # Reset index pour éviter ValueError avec labels dupliqués (concat multi-batches)
    df = df.copy().reset_index(drop=True)

    # Vérifier que boundaries_gdf est utilisable
    if boundaries_gdf is None or boundaries_gdf.empty:
        logger.warning("boundaries_gdf vide — sjoin impossible, arrondissement à NA")
        df["arrondissement"] = pd.NA
        return df

    if "arrondissement" not in boundaries_gdf.columns:
        candidates = [
            c for c in boundaries_gdf.columns
            if any(kw in c.lower() for kw in ("arr", "c_ar", "num_arr", "arrondis"))
        ]
        if candidates:
            boundaries_gdf = boundaries_gdf.rename(columns={candidates[0]: "arrondissement"})
            logger.info("Colonne boundaries renommée : '%s' → 'arrondissement'", candidates[0])
        else:
            logger.error(
                "boundaries_gdf sans colonne 'arrondissement'. Colonnes : %s",
                list(boundaries_gdf.columns),
            )
            df["arrondissement"] = pd.NA
            return df

    mask = df[lat_col].notna() & df[lon_col].notna()
    valid = df[mask].copy()
    if valid.empty:
        logger.warning("Aucun point avec coordonnées valides pour le sjoin")
        df["arrondissement"] = pd.NA
        return df

    # Supprimer la colonne 'arrondissement' du GDF de gauche si elle existe déjà
    # pour éviter que geopandas la renomme en 'arrondissement_left'/'arrondissement_right'
    valid_for_join = valid.drop(columns=["arrondissement"], errors="ignore")

    gdf_pts = gpd.GeoDataFrame(
        valid_for_join,
        geometry=gpd.points_from_xy(valid_for_join[lon_col], valid_for_join[lat_col]),
        crs="EPSG:4326",
    )
    joined = gpd.sjoin(
        gdf_pts,
        boundaries_gdf[["arrondissement", "geometry"]],
        how="left",
        predicate="within",
    )
    # gpd.sjoin peut dupliquer si un point touche deux polygones (rare)
    joined = joined[~joined.index.duplicated(keep="first")]

    # Sécurité : gérer le renommage residuel _right
    if "arrondissement" not in joined.columns and "arrondissement_right" in joined.columns:
        joined = joined.rename(columns={"arrondissement_right": "arrondissement"})

    if "arrondissement" not in joined.columns:
        logger.warning("sjoin : colonne 'arrondissement' absente du résultat (%s)", list(joined.columns)[:8])
        df["arrondissement"] = pd.NA
    vals = joined["arrondissement"].reindex(valid.index)
    vals = vals.astype(object).where(vals.notna(), pd.NA)
    if "arrondissement" in df.columns:
        target_dtype = str(df["arrondissement"].dtype)
        if target_dtype.startswith("string") or target_dtype == "str":
            vals = vals.apply(
                lambda x: str(int(x)) if isinstance(x, (int, float)) and pd.notna(x) else (str(x) if pd.notna(x) else pd.NA)
            )
        else:
            try:
                vals = vals.astype(df["arrondissement"].dtype)
            except Exception:
                pass
    df.loc[valid.index, "arrondissement"] = vals
    return df


def _load_iris_gdf(logger: logging.Logger) -> gpd.GeoDataFrame:
    """Charge les polygones IRIS depuis le Bronze iris_boundaries.

    Retourne un GeoDataFrame [code_iris, arrondissement, nom_iris, geometry]
    en EPSG:4326. Squelette des ~992 IRIS parisiens pour les jointures spatiales.
    """
    df = read_parquet("iris_boundaries")
    if df.empty:
        logger.error("Bronze iris_boundaries vide — exécuter ingest_iris_boundaries() d'abord")
        return gpd.GeoDataFrame()

    geom_col = "geometry_wkt" if "geometry_wkt" in df.columns else "geometry"
    try:
        geom = gpd.GeoSeries.from_wkt(df[geom_col])
    except Exception as exc:
        logger.error("Impossible de parser la géométrie IRIS : %s", exc)
        return gpd.GeoDataFrame()

    gdf = gpd.GeoDataFrame(df, geometry=geom, crs="EPSG:4326")
    keep = [c for c in ["code_iris", "arrondissement", "nom_iris"] if c in gdf.columns]
    logger.info("IRIS chargés : %d polygones", len(gdf))
    return gdf[keep + ["geometry"]].copy()


def _sjoin_points_to_iris(
    df: pd.DataFrame,
    lat_col: str,
    lon_col: str,
    iris_gdf: gpd.GeoDataFrame,
    logger: logging.Logger,
) -> pd.DataFrame:
    """Jointure spatiale point-dans-polygone IRIS.

    Retourne *df* enrichi des colonnes 'code_iris' et 'arrondissement'.
    Généralise `_sjoin_to_arrondissement` à la maille IRIS (clé code_iris).
    """
    df = df.copy().reset_index(drop=True)
    # Dédupliquer d'éventuelles colonnes en double (ex. lat/latitude des batches Vélib')
    df = df.loc[:, ~df.columns.duplicated()]
    if iris_gdf is None or iris_gdf.empty:
        logger.warning("iris_gdf vide — sjoin IRIS impossible, code_iris à NA")
        df["code_iris"] = pd.NA
        df["arrondissement"] = pd.NA
        return df

    mask = df[lat_col].notna() & df[lon_col].notna()
    valid = df[mask].copy()
    if valid.empty:
        logger.warning("Aucun point avec coordonnées valides pour le sjoin IRIS")
        df["code_iris"] = pd.NA
        df["arrondissement"] = pd.NA
        return df

    valid_for_join = valid.drop(columns=["code_iris", "arrondissement"], errors="ignore")
    gdf_pts = gpd.GeoDataFrame(
        valid_for_join,
        geometry=gpd.points_from_xy(valid_for_join[lon_col], valid_for_join[lat_col]),
        crs="EPSG:4326",
    )
    join_cols = [c for c in ["code_iris", "arrondissement"] if c in iris_gdf.columns]
    joined = gpd.sjoin(
        gdf_pts, iris_gdf[join_cols + ["geometry"]],
        how="left", predicate="within",
    )
    joined = joined[~joined.index.duplicated(keep="first")]

    for col in join_cols:
        right = col if col in joined.columns else f"{col}_right"
        if right in joined.columns:
            vals = joined[right].reindex(valid.index)
            vals = vals.astype(object).where(vals.notna(), pd.NA)
            if col in df.columns:
                target_dtype = str(df[col].dtype)
                if target_dtype.startswith("string") or target_dtype == "str":
                    vals = vals.apply(
                        lambda x: str(int(x)) if isinstance(x, (int, float)) and pd.notna(x) else (str(x) if pd.notna(x) else pd.NA)
                    )
                else:
                    try:
                        vals = vals.astype(df[col].dtype)
                    except Exception:
                        pass
            df.loc[valid.index, col] = vals
    if "code_iris" not in df.columns:
        df["code_iris"] = pd.NA
    if "arrondissement" not in df.columns:
        df["arrondissement"] = pd.NA
    return df


def _pollen_risk_level(total: float | None) -> str | None:
    """Niveau de risque pollinique à partir du pic total (grains/m³).

    Mêmes seuils que l'ingestion Open-Meteo, recalculés ici sur la moyenne
    par arrondissement (métrique détaillée — n'alimente aucun score).
    """
    if total is None or pd.isna(total):
        return None
    if total < 10:
        return "Faible"
    if total < 50:
        return "Modéré"
    if total < 150:
        return "Élevé"
    return "Très élevé"


def _commune_to_arrondissement(commune_code: pd.Series) -> pd.Series:
    """Cas 1 — extrait l'arrondissement depuis le code INSEE parisien (75101→1)."""
    codes = commune_code.astype(str).str.strip()
    paris_mask = codes.str.match(r"^751\d{2}$")
    arr = pd.Series(pd.NA, index=commune_code.index, dtype="Int64")
    arr[paris_mask] = codes[paris_mask].str[-2:].astype(int)
    return arr


def _save_silver(df: pd.DataFrame, filename: str, logger: logging.Logger) -> Path:
    """Persiste un DataFrame Silver en Parquet."""
    SILVER_ROOT.mkdir(parents=True, exist_ok=True)
    path = SILVER_ROOT / filename
    df.to_parquet(path, index=False, engine="pyarrow", compression="snappy")
    logger.info("Silver → %d lignes : %s", len(df), path)
    return path


def _empty_arr_df(extra_cols: list[str]) -> pd.DataFrame:
    """DataFrame vide avec le squelette attendu (arrondissements 1-20)."""
    base = pd.DataFrame({"arrondissement": PARIS_ARRONDISSEMENTS})
    for col in extra_cols:
        base[col] = pd.NA
    return base


# ---------------------------------------------------------------------------
# Indicateur 1 — Connectivité & Télétravail
# ---------------------------------------------------------------------------

def build_connectivity_silver(logger: logging.Logger | None = None) -> pd.DataFrame:
    """
    Agrège les données ARCEP mobile, ARCEP fibre et INSEE logements
    au niveau arrondissement (cas 1 : commune_code).

    Schéma sortant
    --------------
    arrondissement, pct_pop_4g_mean, pct_pop_5g_mean,
    operateurs_4g_count, operateurs_5g_count,
    pct_eligible_ftth, nb_t2, nb_t3, pct_t2_t3, computed_at
    """
    log = logger or get_logger("silver.connectivity", LOG_DIR)
    computed_at = datetime.now(timezone.utc)

    # Squelette de base (20 arrondissements garantis en sortie)
    base = pd.DataFrame({"arrondissement": PARIS_ARRONDISSEMENTS})

    # --- ARCEP Mobile ---
    df_mob = read_parquet("arcep_mobile")
    if not df_mob.empty and (
        "commune_code" in df_mob.columns or "arrondissement" in df_mob.columns
    ):
        # Le Bronze mobile porte déjà l'arrondissement (issu d'un sjoin) ; le
        # commune_code est "75056" (Paris global) → on privilégie l'arrondissement.
        if "arrondissement" in df_mob.columns and pd.to_numeric(
            df_mob["arrondissement"], errors="coerce"
        ).notna().any():
            df_mob["arrondissement"] = pd.to_numeric(df_mob["arrondissement"], errors="coerce")
        else:
            df_mob["arrondissement"] = _commune_to_arrondissement(df_mob["commune_code"])
        df_mob = df_mob.dropna(subset=["arrondissement"])
        df_mob["arrondissement"] = df_mob["arrondissement"].astype(int)

        mob_agg = (
            df_mob.groupby("arrondissement")
            .agg(
                pct_pop_4g_mean=("pct_pop_4g", "mean"),
                pct_pop_5g_mean=("pct_pop_5g", "mean"),
                operateurs_4g_count=("has_4g", lambda s: s.astype(bool).sum()),
                operateurs_5g_count=("has_5g", lambda s: s.astype(bool).sum()),
            )
            .reset_index()
        )
        base = base.merge(mob_agg, on="arrondissement", how="left")
        log.info("ARCEP mobile agrégé : %d arrondissements", mob_agg["arrondissement"].nunique())
    else:
        log.warning("Bronze arcep_mobile vide ou mal formé — colonnes mobile à NA")
        for col in ["pct_pop_4g_mean", "pct_pop_5g_mean", "operateurs_4g_count", "operateurs_5g_count"]:
            base[col] = pd.NA

    # --- ARCEP Fibre ---
    df_fib = read_parquet("arcep_fibre")
    if not df_fib.empty and "commune_code" in df_fib.columns:
        df_fib["arrondissement"] = _commune_to_arrondissement(df_fib["commune_code"])
        df_fib = df_fib.dropna(subset=["arrondissement"])
        df_fib["arrondissement"] = df_fib["arrondissement"].astype(int)

        fib_agg = (
            df_fib.groupby("arrondissement")
            .agg(
                pct_eligible_ftth=("pct_eligible_ftth", "mean"),
                nb_local_ftth=("nb_local_ftth", "sum"),
            )
            .reset_index()
        )
        base = base.merge(fib_agg, on="arrondissement", how="left")
        log.info("ARCEP fibre agrégé : %d arrondissements", fib_agg["arrondissement"].nunique())
    else:
        log.warning("Bronze arcep_fibre vide — colonnes fibre à NA")
        base["pct_eligible_ftth"] = pd.NA
        base["nb_local_ftth"] = pd.NA

    # --- INSEE Logements ---
    df_log = read_parquet("insee_logements")
    if not df_log.empty and "commune_code" in df_log.columns:
        df_log["arrondissement"] = _commune_to_arrondissement(df_log["commune_code"])
        df_log = df_log.dropna(subset=["arrondissement"])
        df_log["arrondissement"] = df_log["arrondissement"].astype(int)

        log_agg = (
            df_log.groupby("arrondissement")
            .agg(
                nb_t2=("nb_t2", "sum"),
                nb_t3=("nb_t3", "sum"),
                nb_logements_total=("nb_logements_total", "sum"),
            )
            .reset_index()
        )
        log_agg["pct_t2_t3"] = (
            (log_agg["nb_t2"] + log_agg["nb_t3"]) / log_agg["nb_logements_total"] * 100
        ).round(2)
        base = base.merge(
            log_agg[["arrondissement", "nb_t2", "nb_t3", "pct_t2_t3"]],
            on="arrondissement", how="left",
        )
        log.info("INSEE logements agrégé : %d arrondissements", log_agg["arrondissement"].nunique())
    else:
        log.warning("Bronze insee_logements vide — colonnes logements à NA")
        base["nb_t2"] = pd.NA
        base["nb_t3"] = pd.NA
        base["pct_t2_t3"] = pd.NA

    base["computed_at"] = computed_at
    return base.sort_values("arrondissement").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Indicateur 2 — Mobilité (Vélib' micro-batch)
# ---------------------------------------------------------------------------

def build_mobility_silver(
    boundaries_gdf: gpd.GeoDataFrame | None = None,
    logger: logging.Logger | None = None,
) -> pd.DataFrame:
    """
    Agrège la disponibilité Vélib' (tous les batches Bronze) par arrondissement
    via jointure spatiale (cas 2).

    Schéma sortant
    --------------
    arrondissement, station_count_velib, avg_bikes_available,
    avg_docks_available, avg_bikes_pct, electric_bike_ratio,
    prim_stop_count, computed_at
    """
    log = logger or get_logger("silver.mobility", LOG_DIR)
    computed_at = datetime.now(timezone.utc)

    base = pd.DataFrame({"arrondissement": PARIS_ARRONDISSEMENTS})

    if boundaries_gdf is None or boundaries_gdf.empty:
        boundaries_gdf = _load_boundaries_gdf(log)

    # --- Vélib' ---
    df_vel = read_parquet("velib")
    if not df_vel.empty:
        # Cas A — colonne 'arrondissement' déjà présente en texte ("Paris 16e", "Hors Paris")
        if "arrondissement" in df_vel.columns and df_vel["arrondissement"].dtype == object:
            df_vel["arrondissement"] = (
                df_vel["arrondissement"].astype(str)
                .str.extract(r"Paris\s+(\d+)", expand=False)
                .pipe(pd.to_numeric, errors="coerce")
                .astype("Int64")
            )
        # Cas B — colonne 'arrondissement' numérique déjà présente (OK tel quel)
        elif "arrondissement" in df_vel.columns:
            df_vel["arrondissement"] = pd.to_numeric(
                df_vel["arrondissement"], errors="coerce"
            ).astype("Int64")
        # Cas C — aucune colonne arrondissement : sjoin lat/lon
        else:
            # Dédupliquer les colonnes lat/lon si le rename créerait des doublons
            df_vel = df_vel.loc[:, ~df_vel.columns.duplicated()]
            df_vel = df_vel.rename(
                columns={"lat": "latitude", "lon": "longitude"}, errors="ignore"
            )
            if {"latitude", "longitude"}.issubset(df_vel.columns):
                df_vel = _sjoin_to_arrondissement(
                    df_vel, "latitude", "longitude", boundaries_gdf, log
                )

        df_vel = df_vel.dropna(subset=["arrondissement"])
        df_vel["arrondissement"] = df_vel["arrondissement"].astype(int)

        # Normaliser noms de colonnes (schémas historiques possibles)
        col_bikes  = next((c for c in ["bikes_available",  "numBikesAvailable"]  if c in df_vel.columns), None)
        col_docks  = next((c for c in ["docks_available",  "numDocksAvailable"]  if c in df_vel.columns), None)
        col_cap    = next((c for c in ["total_capacity",   "capacity"]           if c in df_vel.columns), None)
        col_elec   = next((c for c in ["electric_bikes",   "numEBikesAvailable"] if c in df_vel.columns), None)
        col_code   = next((c for c in ["station_code",     "stationCode"]        if c in df_vel.columns), None)

        if col_bikes:
            df_vel["bikes_pct"] = (
                pd.to_numeric(df_vel[col_bikes], errors="coerce") /
                pd.to_numeric(df_vel[col_cap], errors="coerce").replace(0, pd.NA) * 100
            ) if col_cap else pd.NA
        if col_elec and col_bikes:
            df_vel["electric_ratio"] = (
                pd.to_numeric(df_vel[col_elec], errors="coerce") /
                pd.to_numeric(df_vel[col_bikes], errors="coerce").replace(0, pd.NA) * 100
            )

        agg_dict = {}
        if col_code:   agg_dict["station_count_velib"]  = (col_code,  "nunique")
        if col_bikes:  agg_dict["avg_bikes_available"]   = (col_bikes, "mean")
        if col_docks:  agg_dict["avg_docks_available"]   = (col_docks, "mean")
        if "bikes_pct" in df_vel.columns:
            agg_dict["avg_bikes_pct"] = ("bikes_pct", "mean")
        if "electric_ratio" in df_vel.columns:
            agg_dict["electric_bike_ratio"] = ("electric_ratio", "mean")

        if agg_dict:
            vel_agg = df_vel.groupby("arrondissement").agg(**agg_dict).reset_index()
            for col in ["avg_bikes_available", "avg_docks_available",
                        "avg_bikes_pct", "electric_bike_ratio"]:
                if col in vel_agg.columns:
                    # to_numeric : certaines colonnes peuvent être 'object' (NA-only)
                    vel_agg[col] = pd.to_numeric(vel_agg[col], errors="coerce").round(2)
            base = base.merge(vel_agg, on="arrondissement", how="left")
            batches = df_vel["batch_ts"].nunique() if "batch_ts" in df_vel.columns else "?"
            log.info("Vélib' agrégé : %d arrondissements, %s batches",
                     vel_agg["arrondissement"].nunique(), batches)
        else:
            log.warning("Vélib' : aucune colonne connue (bikes_available…)")
            for col in ["station_count_velib", "avg_bikes_available",
                        "avg_docks_available", "avg_bikes_pct", "electric_bike_ratio"]:
                base[col] = pd.NA
    else:
        log.warning("Bronze velib vide — colonnes mobilité à NA")
        for col in ["station_count_velib", "avg_bikes_available", "avg_docks_available",
                    "avg_bikes_pct", "electric_bike_ratio"]:
            base[col] = pd.NA

    # --- ICAR Référentiel (cas 2 : sjoin lat/lon) ---
    df_icar = read_parquet("icar")
    _transit_cols = ["transit_stop_count", "metro_count", "rer_count", "tram_count", "bus_count"]
    if not df_icar.empty and {"latitude", "longitude", "transport_mode"}.issubset(df_icar.columns):
        # Conserver uniquement le batch le plus récent (référentiel quasi-statique)
        if "batch_ts" in df_icar.columns:
            latest_batch = df_icar["batch_ts"].max()
            df_icar = df_icar[df_icar["batch_ts"] == latest_batch].copy()
            log.info("ICAR : batch le plus récent sélectionné (%s)", latest_batch)

        # Filtrer les coordonnées nulles
        df_icar = df_icar.dropna(subset=["latitude", "longitude"]).reset_index(drop=True)

        if not df_icar.empty:
            # Jointure spatiale → colonne 'arrondissement'
            df_icar = _sjoin_to_arrondissement(
                df_icar, "latitude", "longitude", boundaries_gdf, log
            )
            df_icar = df_icar.dropna(subset=["arrondissement"])
            df_icar["arrondissement"] = df_icar["arrondissement"].astype(int)

            # Comptage total par arrondissement
            transit_total = (
                df_icar.groupby("arrondissement")
                .size()
                .reset_index(name="transit_stop_count")
            )

            # Comptage par mode via pivot (unstack garantit une colonne par mode)
            mode_pivot = (
                df_icar.groupby(["arrondissement", "transport_mode"])
                .size()
                .unstack(fill_value=0)
                .reset_index()
            )
            # Garantir les 4 colonnes attendues même si un mode est absent des données
            for mode in ["metro", "rer", "tram", "bus"]:
                if mode not in mode_pivot.columns:
                    mode_pivot[mode] = 0
            mode_pivot = mode_pivot.rename(columns={
                "metro": "metro_count",
                "rer":   "rer_count",
                "tram":  "tram_count",
                "bus":   "bus_count",
            })
            # Garder uniquement les colonnes utiles (ignorer "unknown", "multimodal", etc.)
            mode_pivot = mode_pivot[
                ["arrondissement"] +
                [c for c in ["metro_count", "rer_count", "tram_count", "bus_count"]
                 if c in mode_pivot.columns]
            ]

            # Fusion total + détail
            icar_agg = transit_total.merge(mode_pivot, on="arrondissement", how="left")

            # Typage entier sur icar_agg avant merge
            for col in _transit_cols:
                if col in icar_agg.columns:
                    icar_agg[col] = icar_agg[col].fillna(0).astype(int)

            base = base.merge(icar_agg, on="arrondissement", how="left")

            # Remplir les arrondissements sans aucun arrêt ICAR (NaN issus du left merge)
            for col in _transit_cols:
                if col in base.columns:
                    base[col] = base[col].fillna(0).astype("Int64")

            log.info(
                "ICAR : %d arrêts répartis dans %d arrondissements "
                "(metro=%d, rer=%d, tram=%d, bus=%d)",
                len(df_icar),
                icar_agg["arrondissement"].nunique(),
                int(icar_agg.get("metro_count", pd.Series([0])).sum()),
                int(icar_agg.get("rer_count",   pd.Series([0])).sum()),
                int(icar_agg.get("tram_count",  pd.Series([0])).sum()),
                int(icar_agg.get("bus_count",   pd.Series([0])).sum()),
            )
        else:
            log.warning("ICAR : aucun arrêt avec coordonnées valides après filtrage")
            for col in _transit_cols:
                base[col] = pd.NA
    else:
        log.warning("Bronze icar vide ou colonnes manquantes — transit à NA")
        for col in _transit_cols:
            base[col] = pd.NA

    base["computed_at"] = computed_at
    return base.sort_values("arrondissement").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Indicateur 3 — Santé Environnementale
# ---------------------------------------------------------------------------

def build_health_env_silver(
    boundaries_gdf: gpd.GeoDataFrame | None = None,
    logger: logging.Logger | None = None,
) -> pd.DataFrame:
    """
    Agrège la qualité de l'air Citeair (cas 1 : groupby arrondissement),
    les îlots de fraîcheur (cas 3 : label) et la canopée (cas 3 : label)
    par arrondissement.

    Schéma sortant
    --------------
    arrondissement, nb_ilots_fraicheur, surface_fraicheur_ha,
    nb_arbres, arbres_per_km2, european_aqi, avg_atmo_index,
    pollen_total, pollen_risk, computed_at
    """
    log = logger or get_logger("silver.health_env", LOG_DIR)
    computed_at = datetime.now(timezone.utc)

    base = pd.DataFrame({"arrondissement": PARIS_ARRONDISSEMENTS})

    if boundaries_gdf is None or boundaries_gdf.empty:
        boundaries_gdf = _load_boundaries_gdf(log)

    # --- Qualité de l'air + pollen (Bronze air_quality — Open-Meteo, par arrondissement) ---
    # european_aqi  → alimente le score Santé Environnementale (via scoring.py)
    # pollen_total / pollen_risk → métriques détaillées uniquement (aucun score)
    df_air = read_parquet("air_quality")
    if not df_air.empty and "arrondissement" in df_air.columns:
        df_air["arrondissement"] = pd.to_numeric(df_air["arrondissement"], errors="coerce")
        # Convertir code INSEE 75101-75120 → int 1-20 si besoin
        mask_insee = df_air["arrondissement"] > 100
        df_air.loc[mask_insee, "arrondissement"] = df_air.loc[mask_insee, "arrondissement"] % 100
        df_air = df_air.dropna(subset=["arrondissement"])
        df_air = df_air[df_air["arrondissement"].between(1, 20)]
        df_air["arrondissement"] = df_air["arrondissement"].astype(int)

        # Agrégats selon le schéma Bronze disponible (Open-Meteo fournit european_aqi + pollen ;
        # les anciennes partitions Airparif n'ont qu'indice_atmo_num → moyenne NaN-safe).
        agg_spec: dict[str, tuple[str, str]] = {}
        if "european_aqi" in df_air.columns:
            agg_spec["european_aqi"] = ("european_aqi", "mean")
        if "indice_atmo_num" in df_air.columns:
            agg_spec["avg_atmo_index"] = ("indice_atmo_num", "mean")
        if "pollen_total" in df_air.columns:
            agg_spec["pollen_total"] = ("pollen_total", "mean")

        if agg_spec:
            air_agg = df_air.groupby("arrondissement").agg(**agg_spec).reset_index()
            for col in ("european_aqi", "avg_atmo_index", "pollen_total"):
                if col in air_agg.columns:
                    air_agg[col] = pd.to_numeric(air_agg[col], errors="coerce").round(1)
            # Risque pollinique dérivé du pic moyen (métrique détaillée)
            if "pollen_total" in air_agg.columns:
                air_agg["pollen_risk"] = air_agg["pollen_total"].apply(_pollen_risk_level)
            base = base.merge(air_agg, on="arrondissement", how="left")
            log.info(
                "Qualité air : %d arrondissements (european_aqi=%s, pollen=%s)",
                air_agg["arrondissement"].nunique(),
                "oui" if "european_aqi" in air_agg.columns else "non",
                "oui" if "pollen_total" in air_agg.columns else "non",
            )
        else:
            log.warning("Bronze air_quality sans colonne air exploitable — colonnes air à NA")
            for col in ["european_aqi", "avg_atmo_index", "pollen_total", "pollen_risk"]:
                base[col] = pd.NA
    else:
        log.warning("Bronze air_quality vide ou mal formé — colonnes air à NA")
        for col in ["european_aqi", "avg_atmo_index", "pollen_total", "pollen_risk"]:
            base[col] = pd.NA

    # --- Îlots de fraîcheur (cas 3 : colonne arrondissement int déjà présente) ---
    df_ilots = read_parquet("paris_ilots_fraicheur")
    if not df_ilots.empty and "arrondissement" in df_ilots.columns:
        df_ilots = df_ilots.dropna(subset=["arrondissement"])
        df_ilots["arrondissement"] = pd.to_numeric(df_ilots["arrondissement"], errors="coerce")
        df_ilots = df_ilots.dropna(subset=["arrondissement"])
        df_ilots["arrondissement"] = df_ilots["arrondissement"].astype(int)

        ilots_agg = (
            df_ilots.groupby("arrondissement")
            .agg(
                nb_ilots_fraicheur=("site_id", "count"),
                surface_fraicheur_ha=("surface_ha", "sum"),
            )
            .reset_index()
        )
        ilots_agg["surface_fraicheur_ha"] = ilots_agg["surface_fraicheur_ha"].round(2)
        base = base.merge(ilots_agg, on="arrondissement", how="left")
        log.info("Îlots de fraîcheur : %d arrondissements", ilots_agg["arrondissement"].nunique())
    else:
        log.warning("Bronze paris_ilots_fraicheur vide — colonnes îlots à NA")
        base["nb_ilots_fraicheur"] = pd.NA
        base["surface_fraicheur_ha"] = pd.NA

    # --- Canopée / Arbres (cas 3 : colonne arrondissement int) ---
    df_canopee = read_parquet("paris_canopee")
    if not df_canopee.empty and "arrondissement" in df_canopee.columns:
        df_canopee = df_canopee.dropna(subset=["arrondissement"])
        df_canopee["arrondissement"] = pd.to_numeric(df_canopee["arrondissement"], errors="coerce")
        df_canopee = df_canopee.dropna(subset=["arrondissement"])
        df_canopee["arrondissement"] = df_canopee["arrondissement"].astype(int)

        can_agg = (
            df_canopee.groupby("arrondissement")
            .agg(nb_arbres=("arbre_id", "count"))
            .reset_index()
        )
        base = base.merge(can_agg, on="arrondissement", how="left")

        # Densité arborée (arbres / km²) via surface connue
        base["arbres_per_km2"] = (
            base["nb_arbres"] / base["arrondissement"].map(_ARR_AREA_KM2)
        ).round(1)
        log.info("Canopée : %d arrondissements", can_agg["arrondissement"].nunique())
    else:
        log.warning("Bronze paris_canopee vide — colonnes canopée à NA")
        base["nb_arbres"] = pd.NA
        base["arbres_per_km2"] = pd.NA

    # Remplir arbres_per_km2 si nb_arbres est disponible mais colonne absente
    if "nb_arbres" in base.columns and "arbres_per_km2" not in base.columns:
        base["arbres_per_km2"] = (
            base["nb_arbres"] / base["arrondissement"].map(_ARR_AREA_KM2)
        ).round(1)

    base["computed_at"] = computed_at
    return base.sort_values("arrondissement").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Indicateur 4 — Tranquillité vs Dynamisme
# ---------------------------------------------------------------------------

def build_tranquility_silver(logger: logging.Logger | None = None) -> pd.DataFrame:
    """
    Agrège Bruitparif (cas 1 : commune_code) et Crime SSMSI (cas 1 : commune_code)
    avec les comptages OSM bars/nightclubs (déjà disponibles en Silver).

    Schéma sortant
    --------------
    arrondissement, crime_count_total, crime_rate_per_1000,
    noise_lden_surface_ha, noise_ln_surface_ha,
    nb_bars, nb_nightclubs, computed_at
    """
    log = logger or get_logger("silver.tranquility", LOG_DIR)
    computed_at = datetime.now(timezone.utc)

    base = pd.DataFrame({"arrondissement": PARIS_ARRONDISSEMENTS})

    # --- Crime SSMSI (cas 1 : commune_code ou arrondissement déjà présent) ---
    df_crime = read_parquet("crime")
    if not df_crime.empty:
        if "arrondissement" not in df_crime.columns and "commune_code" in df_crime.columns:
            df_crime["arrondissement"] = _commune_to_arrondissement(df_crime["commune_code"])

        df_crime = df_crime.dropna(subset=["arrondissement"])
        df_crime["arrondissement"] = df_crime["arrondissement"].astype(int)

        # Dernière année disponible uniquement (évite les doublons inter-années)
        if "year" in df_crime.columns:
            latest_year = df_crime["year"].max()
            df_crime = df_crime[df_crime["year"] == latest_year]

        crime_agg = (
            df_crime.groupby("arrondissement")
            .agg(
                crime_count_total=("crime_count", "sum"),
                crime_rate_per_1000=("rate_per_1000", "mean"),
            )
            .reset_index()
        )
        crime_agg["crime_count_total"] = crime_agg["crime_count_total"].fillna(0).astype(int)
        crime_agg["crime_rate_per_1000"] = crime_agg["crime_rate_per_1000"].round(2)
        base = base.merge(crime_agg, on="arrondissement", how="left")
        log.info("Crime SSMSI : %d arrondissements (année %s)",
                 crime_agg["arrondissement"].nunique(),
                 df_crime["year"].max() if "year" in df_crime.columns else "?")
    else:
        log.warning("Bronze crime vide — colonnes crime à NA")
        base["crime_count_total"] = pd.NA
        base["crime_rate_per_1000"] = pd.NA

    # --- Bruitparif CBS (cas 1 : commune_code) ---
    df_bruit = read_parquet("bruitparif")
    if not df_bruit.empty and "commune_code" in df_bruit.columns:
        df_bruit["arrondissement"] = _commune_to_arrondissement(df_bruit["commune_code"])
        df_bruit = df_bruit.dropna(subset=["arrondissement"])
        df_bruit["arrondissement"] = df_bruit["arrondissement"].astype(int)

        # Surface exposée Lden (jour-soir-nuit, ≥55 dB)
        lden = df_bruit[df_bruit["indicateur"] == "Lden"] if "indicateur" in df_bruit.columns else df_bruit
        ln   = df_bruit[df_bruit["indicateur"] == "Ln"]   if "indicateur" in df_bruit.columns else pd.DataFrame()

        if not lden.empty and "surface_ha" in lden.columns:
            lden_agg = lden.groupby("arrondissement")["surface_ha"].sum().round(2).reset_index()
            lden_agg.columns = ["arrondissement", "noise_lden_surface_ha"]
            base = base.merge(lden_agg, on="arrondissement", how="left")
        else:
            base["noise_lden_surface_ha"] = pd.NA

        if not ln.empty and "surface_ha" in ln.columns:
            ln_agg = ln.groupby("arrondissement")["surface_ha"].sum().round(2).reset_index()
            ln_agg.columns = ["arrondissement", "noise_ln_surface_ha"]
            base = base.merge(ln_agg, on="arrondissement", how="left")
        else:
            base["noise_ln_surface_ha"] = pd.NA

        log.info("Bruitparif : %d arrondissements", df_bruit["arrondissement"].nunique())
    else:
        log.warning("Bronze bruitparif vide — colonnes bruit à NA")
        base["noise_lden_surface_ha"] = pd.NA
        base["noise_ln_surface_ha"] = pd.NA

    # --- OSM bars / nightclubs (depuis Bronze osm) ---
    df_osm = read_parquet("osm")
    if not df_osm.empty and "amenity_type" in df_osm.columns:
        # Arrondissement dérivé de la latitude (heuristique bbox) si absent
        bars  = df_osm[df_osm["amenity_type"] == "bar"]
        clubs = df_osm[df_osm["amenity_type"] == "nightclub"]

        # Compter par arrondissement via la colonne si disponible
        # Sinon la jointure spatiale est faite dans aggregation.py (score_anime existant)
        if "arrondissement" in df_osm.columns:
            bar_c  = bars.groupby("arrondissement").size().reset_index(name="nb_bars")
            club_c = clubs.groupby("arrondissement").size().reset_index(name="nb_nightclubs")
            base = base.merge(bar_c,  on="arrondissement", how="left")
            base = base.merge(club_c, on="arrondissement", how="left")
        else:
            base["nb_bars"] = pd.NA
            base["nb_nightclubs"] = pd.NA
    else:
        log.warning("Bronze osm vide — nb_bars et nb_nightclubs à NA")
        base["nb_bars"] = pd.NA
        base["nb_nightclubs"] = pd.NA

    base["computed_at"] = computed_at
    return base.sort_values("arrondissement").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Orchestrateur Silver
# ---------------------------------------------------------------------------

def build_all_indicator_silvers(logger: logging.Logger | None = None) -> dict[str, pd.DataFrame]:
    """
    Construit les 4 tables Silver d'indicateurs et les persiste en Parquet.

    Retourne un dict {nom: DataFrame} pour que aggregation.py puisse les chaîner.
    """
    log = logger or get_logger("silver.indicators", LOG_DIR)

    # Charger les boundaries une seule fois (partagées par les sjoin)
    boundaries_gdf = _load_boundaries_gdf(log)

    results: dict[str, pd.DataFrame] = {}

    tasks = [
        ("connectivity",  build_connectivity_silver,  {"logger": log},                          "connectivity_by_arrondissement.parquet"),
        ("mobility",      build_mobility_silver,       {"boundaries_gdf": boundaries_gdf, "logger": log}, "mobility_by_arrondissement.parquet"),
        ("health_env",    build_health_env_silver,     {"boundaries_gdf": boundaries_gdf, "logger": log}, "health_env_by_arrondissement.parquet"),
        ("tranquility",   build_tranquility_silver,    {"logger": log},                          "tranquility_by_arrondissement.parquet"),
    ]

    for name, fn, kwargs, filename in tasks:
        log.info("--- Silver : %s ---", name)
        try:
            df = fn(**kwargs)
            _save_silver(df, filename, log)
            results[name] = df
        except Exception as exc:
            log.error("Erreur Silver '%s' : %s", name, exc, exc_info=True)
            results[name] = pd.DataFrame()

    return results
