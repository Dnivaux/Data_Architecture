"""
Silver Layer — Scoring normalisé 0-100 par arrondissement
==========================================================
Produit 4 scores stratégiques + les 3 scores historiques du projet,
tous normalisés en min-max sur [0, 100].

Scores historiques (révisés pour éviter la duplication) :
  - score_anime          : densité bars / nightclubs / parcs (OSM)
  - score_calme          : [déprécié] None — logique bruit fusionnée dans tranquility

Nouveaux scores stratégiques :
  - score_connectivity   : fibre + 4G/5G + ratio T2-T3
  - score_mobility       : transports ICAR par mode (60%) + densité stations Vélib' (40%)
  - score_health_env     : air_quality (40%) + végétalisation (25%) + arbres (20%) + îlots (15%)
  - score_tranquility    : inverse (crime 40% + bruit 35% + bars/clubs 25%)

Changements v2 (2026-05-21) :
  ✓ Suppression de la duplication "crime" entre calme et tranquility
  ✓ Redéfinition de calme : SEULEMENT bruit (meilleure sémantique)
  ✓ Intégration qualité air dans health_env (santé = air pur)

Changements v3 (2026-06-22) :
  ✓ Réintégration de la criminalité dans tranquility (40%) — la sécurité compte
  ✓ Suppression de l'indicateur Accessibilité : le prix DVF médian reste exposé
    comme métrique brute (median_price) mais ne produit plus de score
  ✓ Mobilité : chaque mode ICAR normalisé séparément (anti-domination RER) ;
    Vélib' réduit au comptage de stations (bornes libres → métrique détaillée)
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


def _rank_normalize(series: pd.Series, invert: bool = False) -> pd.Series:
    """
    Normalisation par RANG sur [0, 100] (percentile).
    Le plus faible → 0, le plus fort → 100, répartition régulière (médiane ~50).
    Contrairement au min-max, insensible aux distributions asymétriques : un seul
    arrondissement « hors-norme » ne tasse plus tous les autres vers le bas.
    Si invert=True, le rang est inversé (valeur haute → score bas).
    """
    s = pd.to_numeric(series, errors="coerce")
    if s.notna().sum() <= 1:
        return pd.Series(50.0, index=series.index)
    ranks = s.rank(method="average")  # moyenne des ex-aequo
    lo, hi = ranks.min(), ranks.max()
    if hi == lo:
        return pd.Series(50.0, index=series.index)
    normalized = (ranks - lo) / (hi - lo) * 100.0
    return ((100.0 - normalized) if invert else normalized).round(1)


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
        """
        Dynamisme de quartier 0-100.
        Composantes pondérées (OSM, chacune min-max) :
          restaurant (30%) + bar (20%) + cinema (20%) + park (15%)
          + nightclub (10%) + stadium (5%)

        Le mélange pondéré sert à classer les arrondissements ; le score final est
        ensuite normalisé par RANG (percentile) → médiane ~50, plus animé = 100.
        Évite l'effet de tassement du min-max sur des comptages très asymétriques
        (ex : le 11e concentre les bars et écrasait tous les autres vers le bas).
        """
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
        for col in ["bar", "nightclub", "park", "cinema", "restaurant", "stadium"]:
            if col not in counts.columns:
                counts[col] = 0

        # Normalisation individuelle de chaque composante avant pondération
        s_restaurant = _normalize(counts["restaurant"])
        s_bar        = _normalize(counts["bar"])
        s_cinema     = _normalize(counts["cinema"])
        s_park       = _normalize(counts["park"])
        s_nightclub  = _normalize(counts["nightclub"])
        s_stadium    = _normalize(counts["stadium"])

        # Mélange pondéré (composantes déjà min-max) → score relatif brut…
        _anime_blend = (
            0.30 * s_restaurant
            + 0.20 * s_bar
            + 0.20 * s_cinema
            + 0.15 * s_park
            + 0.10 * s_nightclub
            + 0.05 * s_stadium
        )
        # …puis normalisation par rang pour une échelle intuitive (médiane ~50, top = 100)
        counts["anime_score"] = _rank_normalize(_anime_blend)

        counts = counts.rename(columns={
            "bar": "bar_count", "nightclub": "nightclub_count", "park": "park_count",
            "cinema": "cinema_count", "restaurant": "restaurant_count", "stadium": "stadium_count",
        })
        return counts[["arrondissement", "bar_count", "nightclub_count", "park_count",
                        "cinema_count", "restaurant_count", "stadium_count", "anime_score"]]

    def score_calme(self) -> pd.DataFrame:
        """
        [DÉPRÉCIÉ v2] Score Calme fusionné dans score_tranquility.
        Retourne calme_score=None pour compatibilité descendante.
        La logique bruit est désormais intégrée à score_tranquility (60%).
        """
        base = pd.DataFrame({"arrondissement": PARIS_ARRONDISSEMENTS})
        base["calme_score"] = None
        return base[["arrondissement", "calme_score"]]

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
        Score Mobilité 0-100 — v3 (ICAR + Vélib').
        Composantes :
          Transports en commun ICAR (60%) — chaque mode normalisé SÉPARÉMENT
          puis pondéré, pour qu'un arrondissement à fort hub RER ne sature plus
          tout le score (il ne plafonne que sa propre sous-composante) :
            metro (40%) + rer (25%) + bus (20%) + tram (15%)
          Densité Vélib' (40%) :
            station_count_velib normalisé.

        avg_bikes_available et avg_docks_available sont conservés comme métriques
        détaillées (dashboard) mais ne pèsent plus dans le score.
        """
        df = _read_silver("mobility_by_arrondissement.parquet")
        base = pd.DataFrame({"arrondissement": PARIS_ARRONDISSEMENTS})
        if df.empty:
            self.logger.warning("Silver mobility absent — score par défaut 50")
            base["mobility_score"] = 50.0
            return base

        base = base.merge(df, on="arrondissement", how="left")

        # --- Vélib' : densité de stations uniquement ---
        s_stations = _normalize(base.get("station_count_velib",  pd.Series([0.0] * 20)))

        # --- Transports en commun ICAR (normalisation intra-mode) ---
        _icar_cols = ["metro_count", "rer_count", "tram_count", "bus_count"]
        if all(c in base.columns for c in _icar_cols):
            s_metro = _normalize(pd.to_numeric(base["metro_count"], errors="coerce").fillna(0))
            s_rer   = _normalize(pd.to_numeric(base["rer_count"],   errors="coerce").fillna(0))
            s_tram  = _normalize(pd.to_numeric(base["tram_count"],  errors="coerce").fillna(0))
            s_bus   = _normalize(pd.to_numeric(base["bus_count"],   errors="coerce").fillna(0))
            # Métro = colonne vertébrale parisienne > RER > bus > tram (périphérique)
            s_transit = 0.40 * s_metro + 0.25 * s_rer + 0.20 * s_bus + 0.15 * s_tram
            self.logger.info(
                "Score mobilité ICAR : modes normalisés séparément "
                "(metro/rer/bus/tram = 40/25/20/15)",
            )
        else:
            self.logger.warning("Colonnes ICAR absentes du Silver mobility — s_transit par défaut 50")
            s_transit = pd.Series([50.0] * len(base))

        base["mobility_score"] = (
            0.60 * s_transit
            + 0.40 * s_stations
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
        Composantes : qualité de l'air european_aqi (40% — facteur dominant),
                      surface_fraicheur_ha (25%), arbres_per_km2 (20%),
                      nb_ilots_fraicheur (15%).

        european_aqi : indice européen Open-Meteo [0-100+] agrégé par arrondissement
        dans le Silver health_env (0 = excellent). invert=True → air pur = score élevé.
        Repli sur avg_atmo_index (Citeair) si european_aqi indisponible.
        Le pollen n'entre PAS dans le score (métrique détaillée uniquement).
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

        # Qualité de l'air — european_aqi (Open-Meteo, 0 = excellent) agrégé en Silver.
        # invert=True : european_aqi bas = bon air = score haut. Repli sur avg_atmo_index.
        _aqi_vals = pd.to_numeric(base.get("european_aqi"), errors="coerce") \
            if "european_aqi" in base.columns else pd.Series(dtype=float)
        if _aqi_vals.notna().any():
            s_air = _normalize(base["european_aqi"], invert=True)
            self.logger.info(
                "Score santé : european_aqi min=%.1f max=%.1f",
                _aqi_vals.min(), _aqi_vals.max(),
            )
        elif "avg_atmo_index" in base.columns and pd.to_numeric(
            base["avg_atmo_index"], errors="coerce"
        ).notna().any():
            s_air = _normalize(base["avg_atmo_index"], invert=True)
            self.logger.info("Score santé : repli avg_atmo_index (european_aqi absent)")
        else:
            self.logger.warning("Aucune métrique air dans Silver health_env — s_air par défaut 50")
            s_air = pd.Series([50.0] * len(base))

        base["health_env_score"] = (
            0.40 * s_air + 0.25 * s_surface + 0.20 * s_arbres + 0.15 * s_ilots
        ).round(1)

        result_cols = [
            "arrondissement",
            "surface_fraicheur_ha",
            "arbres_per_km2",
            "nb_ilots_fraicheur",
            "european_aqi",
            "avg_atmo_index",
            "health_env_score",
        ]
        return base[[c for c in result_cols if c in base.columns]]

    def score_tranquility(self) -> pd.DataFrame:
        """
        Score Tranquillité (Sécurité + Calme + Vie nocturne) v3 — 0-100.

        Composantes (inversées : valeur haute = nuisance → score bas) :
          - criminalité (taux /1000 hab)          → 40%
          - bruit Lden (surface exposée ≥55 dB)   → 35%  [ancien Calme]
          - densité bars + boîtes de nuit          → 25%

        Score 100 = arrondissement sûr, silencieux, peu de vie nocturne.
        Score   0 = arrondissement à forte délinquance, bruyant, dense en bars/clubs.
        """
        df = _read_silver("tranquility_by_arrondissement.parquet")
        base = pd.DataFrame({"arrondissement": PARIS_ARRONDISSEMENTS})
        if df.empty:
            self.logger.warning("Silver tranquility absent — score par défaut 50")
            base["tranquility_score"] = 50.0
            return base

        base = base.merge(df, on="arrondissement", how="left")

        # Criminalité : taux /1000 hab (corrige la taille de population), avec
        # repli sur le comptage brut si le taux est absent. Plus bas = plus tranquille.
        if "crime_rate_per_1000" in base.columns and pd.to_numeric(
            base["crime_rate_per_1000"], errors="coerce"
        ).notna().any():
            s_crime = _normalize(base["crime_rate_per_1000"], invert=True)
        else:
            s_crime = _normalize(
                base.get("crime_count_total", pd.Series([0.0] * 20)), invert=True
            )

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

        base["tranquility_score"] = (
            0.40 * s_crime + 0.35 * s_bruit + 0.25 * s_nightlife
        ).round(1)

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
        connectivity  = self.score_connectivity()
        mobility      = self.score_mobility()
        health_env    = self.score_health_env()
        tranquility   = self.score_tranquility()

        base = pd.DataFrame({"arrondissement": PARIS_ARRONDISSEMENTS})
        for df in [anime, calme, connectivity,
                   mobility, health_env, tranquility]:
            if not df.empty:
                base = base.merge(df, on="arrondissement", how="left")

        base["computed_at"] = datetime.now(timezone.utc)
        self.logger.info("Scores calculés pour %d arrondissements", len(base))
        return base


# ---------------------------------------------------------------------------
# Scoring à la maille IRIS (grain primaire)
# ---------------------------------------------------------------------------

class IrisScorer:
    """Calcule les scores de vivabilité par IRIS (~992 zones).

    Réutilise les normalisations `_normalize` / `_rank_normalize`, désormais
    appliquées sur ~992 IRIS (bien plus discriminant que 20 arrondissements).
    Lit les tables Silver `*_by_iris.parquet` produites par `iris_layer.py`.
    Les composantes rediffusées (air, crime, bruit, connectivité) sont
    constantes au sein d'un arrondissement ; les composantes IRIS-natives
    (animation OSM, mobilité, prix, revenus) introduisent la variation interne.
    """

    def __init__(self, logger: logging.Logger | None = None):
        self.logger = logger or get_logger("scoring_iris", LOG_DIR)
        self.base = _read_silver("iris_base.parquet")

    def _merge_base(self, df: pd.DataFrame) -> pd.DataFrame:
        skeleton = self.base[["code_iris", "arrondissement"]].copy()
        if df.empty:
            return skeleton
        cols = [c for c in df.columns if c not in ("arrondissement", "granularite")]
        return skeleton.merge(df[cols], on="code_iris", how="left")

    def score_anime(self) -> pd.DataFrame:
        df = self._merge_base(_read_silver("amenities_by_iris.parquet"))
        for col in ["bar_count", "nightclub_count", "park_count",
                    "cinema_count", "restaurant_count", "stadium_count"]:
            if col not in df.columns:
                df[col] = 0
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        blend = (
            0.30 * _normalize(df["restaurant_count"])
            + 0.20 * _normalize(df["bar_count"])
            + 0.20 * _normalize(df["cinema_count"])
            + 0.15 * _normalize(df["park_count"])
            + 0.10 * _normalize(df["nightclub_count"])
            + 0.05 * _normalize(df["stadium_count"])
        )
        df["anime_score"] = _rank_normalize(blend)
        return df[["code_iris", "bar_count", "nightclub_count", "park_count",
                   "cinema_count", "restaurant_count", "stadium_count", "anime_score"]]

    def score_connectivity(self) -> pd.DataFrame:
        df = self._merge_base(_read_silver("connectivity_by_iris.parquet"))
        df["connectivity_score"] = (
            0.40 * _normalize(df.get("pct_eligible_ftth"))
            + 0.30 * _normalize(df.get("pct_pop_4g_mean"))
            + 0.15 * _normalize(df.get("pct_pop_5g_mean"))
            + 0.15 * _normalize(df.get("pct_t2_t3"))
        ).round(1)
        keep = ["code_iris", "pct_eligible_ftth", "pct_pop_4g_mean",
                "pct_pop_5g_mean", "pct_t2_t3", "connectivity_score"]
        return df[[c for c in keep if c in df.columns]]

    def score_mobility(self) -> pd.DataFrame:
        df = self._merge_base(_read_silver("mobility_by_iris.parquet"))
        s_stations = _normalize(pd.to_numeric(df.get("station_count_velib"), errors="coerce").fillna(0))
        modes = ["metro_count", "rer_count", "tram_count", "bus_count"]
        if all(c in df.columns for c in modes):
            s_transit = (
                0.40 * _normalize(pd.to_numeric(df["metro_count"], errors="coerce").fillna(0))
                + 0.25 * _normalize(pd.to_numeric(df["rer_count"], errors="coerce").fillna(0))
                + 0.20 * _normalize(pd.to_numeric(df["bus_count"], errors="coerce").fillna(0))
                + 0.15 * _normalize(pd.to_numeric(df["tram_count"], errors="coerce").fillna(0))
            )
        else:
            s_transit = pd.Series([50.0] * len(df), index=df.index)
        df["mobility_score"] = (0.60 * s_transit + 0.40 * s_stations).round(1)
        keep = ["code_iris", "station_count_velib", "avg_bikes_available",
                "transit_stop_count", "metro_count", "rer_count",
                "tram_count", "bus_count", "mobility_score"]
        return df[[c for c in keep if c in df.columns]]

    def score_health_env(self) -> pd.DataFrame:
        df = self._merge_base(_read_silver("health_env_by_iris.parquet"))
        s_surface = _normalize(df.get("surface_fraicheur_ha"))
        s_arbres = _normalize(df.get("arbres_per_km2"))
        s_ilots = _normalize(df.get("nb_ilots_fraicheur"))
        if "european_aqi" in df.columns and pd.to_numeric(df["european_aqi"], errors="coerce").notna().any():
            s_air = _normalize(df["european_aqi"], invert=True)
        elif "avg_atmo_index" in df.columns:
            s_air = _normalize(df["avg_atmo_index"], invert=True)
        else:
            s_air = pd.Series([50.0] * len(df), index=df.index)
        df["health_env_score"] = (
            0.40 * s_air + 0.25 * s_surface + 0.20 * s_arbres + 0.15 * s_ilots
        ).round(1)
        keep = ["code_iris", "surface_fraicheur_ha", "arbres_per_km2",
                "nb_ilots_fraicheur", "european_aqi", "avg_atmo_index", "health_env_score"]
        return df[[c for c in keep if c in df.columns]]

    def score_tranquility(self) -> pd.DataFrame:
        df = self._merge_base(_read_silver("tranquility_by_iris.parquet"))
        if "crime_rate_per_1000" in df.columns and pd.to_numeric(df["crime_rate_per_1000"], errors="coerce").notna().any():
            s_crime = _normalize(df["crime_rate_per_1000"], invert=True)
        else:
            s_crime = _normalize(df.get("crime_count_total"), invert=True)
        s_bruit = _normalize(df.get("noise_lden_surface_ha"), invert=True)
        nightlife = (
            pd.to_numeric(df.get("nb_bars"), errors="coerce").fillna(0)
            + pd.to_numeric(df.get("nb_nightclubs"), errors="coerce").fillna(0)
        )
        s_nightlife = _normalize(nightlife, invert=True)
        df["tranquility_score"] = (
            0.40 * s_crime + 0.35 * s_bruit + 0.25 * s_nightlife
        ).round(1)
        keep = ["code_iris", "crime_count_total", "crime_rate_per_1000",
                "noise_lden_surface_ha", "nb_bars", "nb_nightclubs", "tranquility_score"]
        return df[[c for c in keep if c in df.columns]]

    def compute_all_scores(self) -> pd.DataFrame:
        """Fusionne tous les scores IRIS + métriques brutes (prix, revenus)."""
        if self.base.empty:
            self.logger.error("iris_base absente — scoring IRIS impossible")
            return pd.DataFrame()

        self.logger.info("Calcul des scores IRIS (%d zones)", len(self.base))
        out = self.base[["code_iris", "arrondissement", "nom_iris"]].copy()
        for df in [self.score_anime(), self.score_connectivity(), self.score_mobility(),
                   self.score_health_env(), self.score_tranquility()]:
            if not df.empty:
                out = out.merge(df, on="code_iris", how="left")

        # Métriques brutes IRIS-natives : prix DVF médian (dernière année) + revenus
        prices = _read_silver("prices_by_iris_year.parquet")
        if not prices.empty and "year" in prices.columns:
            latest = prices[prices["year"] == prices["year"].max()]
            out = out.merge(
                latest[["code_iris", "median_price"]], on="code_iris", how="left"
            )
        revenus = _read_silver("revenus_by_iris.parquet")
        if not revenus.empty:
            out = out.merge(
                revenus[["code_iris", "median_income", "gini_coefficient", "poverty_rate"]],
                on="code_iris", how="left",
            )

        out["computed_at"] = datetime.now(timezone.utc)
        self.logger.info("Scores IRIS calculés pour %d zones", len(out))
        return out
