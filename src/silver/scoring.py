"""
Silver Layer — Scoring normalisé 0-100 par arrondissement
==========================================================
Produit 4 scores stratégiques + les 3 scores historiques du projet,
tous normalisés en min-max sur [0, 100].

Scores historiques (révisés pour éviter la duplication) :
  - score_anime          : densité bars / nightclubs / parcs (OSM)
  - score_calme          : inverse bruit Lden SONLY (score 100 = très calme/silencieux)
  - score_accessibilite  : inverse prix DVF + logement social

Nouveaux scores stratégiques :
  - score_connectivity   : fibre + 4G/5G + ratio T2-T3
  - score_mobility       : transports en commun ICAR (50%) + Vélib' (50%)
  - score_health_env     : végétalisation (30%) + arbres (30%) + îlots (20%) + air_quality (20%)
  - score_tranquility    : inverse (crime 40% + bruit 35% + bars/clubs 25%)

Changements v2 (2026-05-21) :
  ✓ Suppression de la duplication "crime" entre calme et tranquility
  ✓ Redéfinition de calme : SEULEMENT bruit (meilleure sémantique)
  ✓ Intégration qualité air dans health_env (santé = air pur)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd

from src.ingestion.base import get_logger, read_parquet

LOG_DIR = Path(__file__).parents[2] / "logs"
SILVER_ROOT = Path(__file__).parents[2] / "data" / "silver"

PARIS_ARRONDISSEMENTS = list(range(1, 21))


# ---------------------------------------------------------------------------
# Utilitaire de normalisation
# ---------------------------------------------------------------------------

def _normalize(series: pd.Series, invert: bool = False) -> pd.Series:
    """
    Normalisation min-max sur [0, 100].
    Si invert=True, une valeur haute donne un score bas (ex : criminalité).
    Les NaN sont remplacés par la médiane avant normalisation.
    """
    s = pd.to_numeric(series, errors="coerce")
    median_val = s.median()
    if not pd.notna(median_val):
        return pd.Series(50.0, index=series.index)
    s = s.fillna(median_val)

    lo, hi = s.min(), s.max()
    if hi == lo:
        return pd.Series(50.0, index=series.index)

    normalized = (s - lo) / (hi - lo) * 100.0
    return (100.0 - normalized) if invert else normalized


def _read_silver(filename: str) -> pd.DataFrame:
    """Charge une table Silver Parquet. Retourne DataFrame vide si absente."""
    path = SILVER_ROOT / filename
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(path, engine="pyarrow")
    except Exception:
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Scoring class (compatible avec aggregation.py existant)
# ---------------------------------------------------------------------------

class ArrondissementScorer:
    """
    Calcule tous les scores de vivabilité par arrondissement.
    Compatible avec l'interface existante de aggregation.py.
    """

    def __init__(self, logger: logging.Logger | None = None):
        self.logger = logger or get_logger("scoring", LOG_DIR)
        self.boundaries_gdf = self._load_boundaries()

    def _load_boundaries(self) -> gpd.GeoDataFrame:
        df = read_parquet("boundaries")
        if df.empty:
            self.logger.error("Boundaries vides — run ingest_boundaries() d'abord")
            return gpd.GeoDataFrame()

        geom_col = "geometry_wkt" if "geometry_wkt" in df.columns else "geometry"
        try:
            geom = gpd.GeoSeries.from_wkt(df[geom_col])
        except Exception as exc:
            self.logger.error("Impossible de parser boundaries : %s", exc)
            return gpd.GeoDataFrame()

        gdf = gpd.GeoDataFrame(df, geometry=geom, crs="EPSG:4326")
        if "arrondissement" not in gdf.columns:
            candidates = [c for c in gdf.columns if "arr" in c.lower()]
            if candidates:
                gdf = gdf.rename(columns={candidates[0]: "arrondissement"})
        self.logger.info("Boundaries chargées : %d polygones", len(gdf))
        return gdf[["arrondissement", "geometry"]].copy()

    # ------------------------------------------------------------------
    # Scores historiques (refactorisés)
    # ------------------------------------------------------------------

    def score_anime(self) -> pd.DataFrame:
        """Densité bars + nightclubs + parcs (OSM). Score 0-100."""
        osm_df = read_parquet("osm")
        if osm_df.empty or self.boundaries_gdf.empty:
            self.logger.warning("Données OSM ou boundaries absentes pour score animé")
            return pd.DataFrame({"arrondissement": PARIS_ARRONDISSEMENTS, "anime_score": 50.0})

        osm_gdf = gpd.GeoDataFrame(
            osm_df,
            geometry=gpd.points_from_xy(osm_df["longitude"], osm_df["latitude"]),
            crs="EPSG:4326",
        )
        joined = gpd.sjoin(
            osm_gdf, self.boundaries_gdf[["arrondissement", "geometry"]],
            how="left", predicate="within",
        )

        counts = (
            joined.groupby(["arrondissement", "amenity_type"])
            .size()
            .unstack(fill_value=0)
            .reset_index()
        )
        for col in ["bar", "nightclub", "park"]:
            if col not in counts.columns:
                counts[col] = 0

        counts["total_poi"] = counts["bar"] + counts["nightclub"] + counts["park"]
        counts["anime_score"] = _normalize(counts["total_poi"]).round(1)
        counts = counts.rename(columns={
            "bar": "bar_count", "nightclub": "nightclub_count", "park": "park_count"
        })
        return counts[["arrondissement", "bar_count", "nightclub_count",
                        "park_count", "total_poi", "anime_score"]]

    def score_calme(self) -> pd.DataFrame:
        """
        [DÉPRÉCIÉ v2] Score Calme fusionné dans score_tranquility.
        Retourne calme_score=None pour compatibilité descendante.
        La logique bruit est désormais intégrée à score_tranquility (60%).
        """
        base = pd.DataFrame({"arrondissement": PARIS_ARRONDISSEMENTS})
        base["calme_score"] = None
        return base[["arrondissement", "calme_score"]]

    def score_accessibilite(self) -> pd.DataFrame:
        """
        Score d'accessibilité financière 0-100. Score 100 = très accessible.

        Composantes (consigne « accessibilité prix vs revenus ») :
          - prix DVF médian inversé                       (40%)
          - effort financier prix/revenu (Filosofi INSEE) (35%)  ← prix relatif au revenu local
          - parc de logements sociaux                     (25%)

        Les composantes absentes (DVF/revenus non ingérés) sont neutralisées :
        le score se rabat alors sur les seules sources disponibles.
        """
        base = pd.DataFrame({"arrondissement": PARIS_ARRONDISSEMENTS})

        # --- 1. Prix DVF médian (€/m²) par arrondissement ---
        dvf_df = read_parquet("dvf_clean")
        if not dvf_df.empty and self.boundaries_gdf is not None and not self.boundaries_gdf.empty:
            dvf_df = dvf_df.dropna(subset=["latitude", "longitude", "valeur_fonciere"])
            dvf_df = dvf_df[dvf_df.get("surface_reelle_bati", 0) > 0].copy()
            dvf_df["prix_m2"] = dvf_df["valeur_fonciere"] / dvf_df["surface_reelle_bati"]
            dvf_gdf = gpd.GeoDataFrame(
                dvf_df,
                geometry=gpd.points_from_xy(dvf_df["longitude"], dvf_df["latitude"]),
                crs="EPSG:4326",
            )
            joined = gpd.sjoin(
                dvf_gdf, self.boundaries_gdf[["arrondissement", "geometry"]],
                how="left", predicate="within",
            )
            price_agg = (
                joined.groupby("arrondissement")["prix_m2"]
                .median().reset_index(name="median_price")
            )
            base = base.merge(price_agg, on="arrondissement", how="left")
        else:
            self.logger.warning("DVF (dvf_clean) ou boundaries vides — prix médian à NA")
            base["median_price"] = pd.NA

        # --- 2. Revenu médian (INSEE Filosofi) ---
        rev = read_parquet("revenus")
        if not rev.empty and "median_income" in rev.columns and "arrondissement" in rev.columns:
            income_agg = (
                rev.groupby("arrondissement")["median_income"]
                .median().reset_index(name="median_income")
            )
            base = base.merge(income_agg, on="arrondissement", how="left")
            self.logger.info("Revenus INSEE intégrés à l'accessibilité (%d arrdts)",
                             income_agg["median_income"].notna().sum())
        else:
            self.logger.warning("Revenus absents — composante effort prix/revenu neutralisée")
            base["median_income"] = pd.NA

        # --- 3. Parc social (stock cumulé) ---
        sh = read_parquet("social_housing")
        if not sh.empty and {"arrondissement", "nombre_logements"} <= set(sh.columns):
            sh_agg = (
                sh.groupby("arrondissement")["nombre_logements"]
                .sum().reset_index(name="social_housing_stock")
            )
            base = base.merge(sh_agg, on="arrondissement", how="left")
        else:
            base["social_housing_stock"] = pd.NA

        # --- Composantes normalisées ---
        s_price = _normalize(base["median_price"], invert=True)  # prix bas = accessible

        # Effort prix/revenu : ratio bas = accessible (besoin des deux séries)
        if base["median_income"].notna().any() and base["median_price"].notna().any():
            ratio = pd.to_numeric(base["median_price"], errors="coerce") / pd.to_numeric(
                base["median_income"], errors="coerce"
            )
            base["price_to_income"] = ratio.round(2)
            s_effort = _normalize(ratio, invert=True)
            w_price, w_effort, w_social = 0.40, 0.35, 0.25
        else:
            base["price_to_income"] = pd.NA
            s_effort = pd.Series(50.0, index=base.index)
            w_price, w_effort, w_social = 0.60, 0.0, 0.40

        s_social = _normalize(base["social_housing_stock"])  # plus de social = accessible

        base["accessibilite_score"] = (
            w_price * s_price + w_effort * s_effort + w_social * s_social
        ).round(1)

        # % logement social conservé pour compat. descendante (non calculable sans parc total)
        base["social_housing_pct"] = pd.NA
        return base[["arrondissement", "median_price", "median_income",
                     "price_to_income", "social_housing_pct", "accessibilite_score"]]

    # ------------------------------------------------------------------
    # Nouveaux scores stratégiques (lus depuis Silver indicators)
    # ------------------------------------------------------------------

    def score_connectivity(self) -> pd.DataFrame:
        """
        Score Connectivité 0-100.
        Composantes : pct_eligible_ftth (40%), pct_pop_4g_mean (30%),
                      pct_pop_5g_mean (15%), pct_t2_t3 (15%).
        """
        df = _read_silver("connectivity_by_arrondissement.parquet")
        base = pd.DataFrame({"arrondissement": PARIS_ARRONDISSEMENTS})
        if df.empty:
            self.logger.warning("Silver connectivity absent — score par défaut 50")
            base["connectivity_score"] = 50.0
            return base

        base = base.merge(df, on="arrondissement", how="left")
        s_fibre = _normalize(base.get("pct_eligible_ftth",  pd.Series([50.0]*20)))
        s_4g    = _normalize(base.get("pct_pop_4g_mean",    pd.Series([50.0]*20)))
        s_5g    = _normalize(base.get("pct_pop_5g_mean",    pd.Series([50.0]*20)))
        s_t2t3  = _normalize(base.get("pct_t2_t3",          pd.Series([50.0]*20)))

        base["connectivity_score"] = (
            0.40 * s_fibre + 0.30 * s_4g + 0.15 * s_5g + 0.15 * s_t2t3
        ).round(1)
        return base[["arrondissement", "pct_eligible_ftth", "pct_pop_4g_mean",
                     "pct_pop_5g_mean", "pct_t2_t3", "connectivity_score"]]

    def score_mobility(self) -> pd.DataFrame:
        """
        Score Mobilité 0-100 — v2 (ICAR + Vélib').
        Composantes :
          Transports en commun ICAR (50%) :
            transit_capacity_raw = metro*3 + rer*3 + tram*2 + bus*1 → normalisé
          Mobilités douces Vélib' (50%) :
            avg_bikes_available (20%) + station_count_velib (15%) + avg_docks_available (15%)
        """
        df = _read_silver("mobility_by_arrondissement.parquet")
        base = pd.DataFrame({"arrondissement": PARIS_ARRONDISSEMENTS})
        if df.empty:
            self.logger.warning("Silver mobility absent — score par défaut 50")
            base["mobility_score"] = 50.0
            return base

        base = base.merge(df, on="arrondissement", how="left")

        # --- Vélib' ---
        s_bikes    = _normalize(base.get("avg_bikes_available",  pd.Series([0.0] * 20)))
        s_stations = _normalize(base.get("station_count_velib",  pd.Series([0.0] * 20)))
        # Bornes libres : moins saturé = meilleur accès
        s_docks    = _normalize(base.get("avg_docks_available",  pd.Series([0.0] * 20)))

        # --- Transports en commun ICAR ---
        _icar_cols = ["metro_count", "rer_count", "tram_count", "bus_count"]
        if all(c in base.columns for c in _icar_cols):
            metro = pd.to_numeric(base["metro_count"], errors="coerce").fillna(0)
            rer   = pd.to_numeric(base["rer_count"],   errors="coerce").fillna(0)
            tram  = pd.to_numeric(base["tram_count"],  errors="coerce").fillna(0)
            bus   = pd.to_numeric(base["bus_count"],   errors="coerce").fillna(0)
            # Capacité pondérée : metro/RER (×3) > tram (×2) > bus (×1)
            transit_capacity_raw = metro * 3 + rer * 3 + tram * 2 + bus
            s_transit = _normalize(transit_capacity_raw)
            self.logger.info(
                "Score mobilité ICAR : capacité transit min=%.0f max=%.0f",
                transit_capacity_raw.min(), transit_capacity_raw.max(),
            )
        else:
            self.logger.warning("Colonnes ICAR absentes du Silver mobility — s_transit par défaut 50")
            s_transit = pd.Series([50.0] * len(base))

        base["mobility_score"] = (
            0.50 * s_transit
            + 0.20 * s_bikes
            + 0.15 * s_stations
            + 0.15 * s_docks
        ).round(1)

        result_cols = [
            "arrondissement",
            "station_count_velib", "avg_bikes_available", "avg_docks_available",
            "transit_stop_count", "metro_count", "rer_count", "tram_count", "bus_count",
            "mobility_score",
        ]
        return base[[c for c in result_cols if c in base.columns]]

    def score_health_env(self) -> pd.DataFrame:
        """
        Score Santé Environnementale 0-100.
        Composantes : surface_fraicheur_ha (25%), arbres_per_km2 (25%),
                      nb_ilots_fraicheur (15%), qualité_air Open-Meteo (25%),
                      pollen Open-Meteo (10%).

        Qualité de l'air & pollen : source Open-Meteo (CAMS Europe, gratuit,
        sans clé) — voir src/ingestion/open_meteo_air.py. L'European AQI et le
        pic pollinique journalier sont inversés (bas = bon → score haut).
        """
        df = _read_silver("health_env_by_arrondissement.parquet")
        base = pd.DataFrame({"arrondissement": PARIS_ARRONDISSEMENTS})
        if df.empty:
            self.logger.warning("Silver health_env absent — score par défaut 50")
            base["health_env_score"] = 50.0
            return base

        base = base.merge(df, on="arrondissement", how="left")

        # Composantes végétales et thermiques
        s_surface = _normalize(base.get("surface_fraicheur_ha", pd.Series([0.0]*20)))
        s_arbres  = _normalize(base.get("arbres_per_km2",       pd.Series([0.0]*20)))
        s_ilots   = _normalize(base.get("nb_ilots_fraicheur",   pd.Series([0.0]*20)))

        # Qualité de l'air & pollen (Open-Meteo). european_aqi : bas = bon.
        df_air = read_parquet("air_quality")
        if not df_air.empty and "arrondissement" in df_air.columns:
            agg_spec = {}
            if "european_aqi" in df_air.columns:
                agg_spec["european_aqi"] = "mean"
            elif "indice_atmo_num" in df_air.columns:   # rétro-compat ancien Airparif
                agg_spec["indice_atmo_num"] = "mean"
            if "pollen_total" in df_air.columns:
                agg_spec["pollen_total"] = "mean"

            air_agg = df_air.groupby("arrondissement").agg(agg_spec).reset_index()
            base = base.merge(air_agg, on="arrondissement", how="left")

            air_col = "european_aqi" if "european_aqi" in base.columns else "indice_atmo_num"
            s_air = _normalize(base.get(air_col, pd.Series([50.0]*len(base))), invert=True)

            if "pollen_total" in base.columns:
                s_pollen = _normalize(base["pollen_total"], invert=True)
                base["pollen_risk"] = base["pollen_total"].apply(self._pollen_label)
            else:
                s_pollen = pd.Series([50.0] * len(base))

            # Renommer en european_aqi pour l'aval (Gold / PG / API)
            if air_col == "indice_atmo_num":
                base = base.rename(columns={"indice_atmo_num": "european_aqi"})
        else:
            self.logger.warning("air_quality absent — air & pollen neutres (50)")
            s_air = pd.Series([50.0] * len(base))
            s_pollen = pd.Series([50.0] * len(base))

        base["health_env_score"] = (
            0.25 * s_surface + 0.25 * s_arbres + 0.15 * s_ilots
            + 0.25 * s_air + 0.10 * s_pollen
        ).round(1)

        result_cols = ["arrondissement", "surface_fraicheur_ha",
                       "arbres_per_km2", "nb_ilots_fraicheur"]
        for opt in ["european_aqi", "pollen_total", "pollen_risk"]:
            if opt in base.columns:
                result_cols.append(opt)
        result_cols.append("health_env_score")

        return base[result_cols]

    @staticmethod
    def _pollen_label(total: float) -> str:
        """Niveau de risque pollinique à partir du total moyen (grains/m³)."""
        if pd.isna(total):
            return "Inconnu"
        if total < 10:
            return "Faible"
        if total < 50:
            return "Modéré"
        if total < 150:
            return "Élevé"
        return "Très élevé"

    def score_tranquility(self) -> pd.DataFrame:
        """
        Score Tranquillité (fusion Calme + Tranquillité) v2 — 0-100.
        Mesure les nuisances sonores et nocturnes UNIQUEMENT.

        Composantes :
          - bruit Lden (surface exposée ≥55 dB)  → 60%  [fusion de l'ancien Calme]
          - densité bars + boîtes de nuit         → 40%

        Crime exclu volontairement : la criminalité reste visible en
        « Métriques détaillées » (crime_count_total, crime_rate_per_1000)
        mais n'influence plus ce score pour ne pas le confondre avec la sécurité.

        Score 100 = arrondissement silencieux, peu de vie nocturne.
        Score   0 = arrondissement bruyant avec forte densité bars/clubs.
        """
        df = _read_silver("tranquility_by_arrondissement.parquet")
        base = pd.DataFrame({"arrondissement": PARIS_ARRONDISSEMENTS})
        if df.empty:
            self.logger.warning("Silver tranquility absent — score par défaut 50")
            base["tranquility_score"] = 50.0
            return base

        base = base.merge(df, on="arrondissement", how="left")

        # Bruit Lden (surface en ha exposée ≥ 55 dB) : plus bas = plus calme
        s_bruit = _normalize(
            base.get("noise_lden_surface_ha", pd.Series([0.0] * 20)), invert=True
        )

        # Vie nocturne : bars + nightclubs (OSM). Plus bas = plus tranquille.
        nightlife = (
            pd.to_numeric(base.get("nb_bars",      pd.Series([0.0] * 20)), errors="coerce").fillna(0)
            + pd.to_numeric(base.get("nb_nightclubs", pd.Series([0.0] * 20)), errors="coerce").fillna(0)
        )
        s_nightlife = _normalize(nightlife, invert=True)

        base["tranquility_score"] = (0.60 * s_bruit + 0.40 * s_nightlife).round(1)

        # crime_count_total et crime_rate_per_1000 conservés comme métriques brutes
        return base[["arrondissement", "crime_count_total", "crime_rate_per_1000",
                     "noise_lden_surface_ha", "nb_bars", "nb_nightclubs",
                     "tranquility_score"]]

    # ------------------------------------------------------------------
    # Orchestrateur global (interface publique pour aggregation.py)
    # ------------------------------------------------------------------

    def compute_all_scores(self) -> pd.DataFrame:
        """
        Calcule tous les scores (historiques + nouveaux) et les fusionne.
        Retourne un DataFrame par arrondissement avec toutes les colonnes.
        """
        self.logger.info("Calcul de tous les scores par arrondissement")

        anime         = self.score_anime()
        calme         = self.score_calme()
        accessibilite = self.score_accessibilite()
        connectivity  = self.score_connectivity()
        mobility      = self.score_mobility()
        health_env    = self.score_health_env()
        tranquility   = self.score_tranquility()

        base = pd.DataFrame({"arrondissement": PARIS_ARRONDISSEMENTS})
        for df in [anime, calme, accessibilite, connectivity,
                   mobility, health_env, tranquility]:
            if not df.empty:
                base = base.merge(df, on="arrondissement", how="left")

        base["computed_at"] = datetime.now(timezone.utc)
        self.logger.info("Scores calculés pour %d arrondissements", len(base))
        return base
