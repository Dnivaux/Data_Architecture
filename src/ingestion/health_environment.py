"""
Santé Environnementale — Bronze Ingestion
==========================================
Sources :
  1. Airparif ArcGIS Open Data — stations de mesure & concentrations annuelles
     https://data-airparif-asso.opendata.arcgis.com/
  2. Paris Open Data — Îlots de fraîcheur (espaces verts ouverts au public)
     https://opendata.paris.fr/explore/dataset/ilots-de-fraicheur-espaces-verts-ouverts-au-public/
  3. Paris Open Data — Arbres de Paris (canopée)
     https://opendata.paris.fr/explore/dataset/les-arbres/

Valeur métier
-------------
Croise la qualité de l'air (NO₂, PM2.5, O₃) avec la densité des espaces de fraîcheur
et la couverture arborée pour mesurer le confort environnemental face aux vagues de chaleur.

Architecture Bronze (Medallion)
--------------------------------
  data/bronze/airparif_stations/date=YYYY-MM-DD/part-0.parquet
  data/bronze/paris_ilots_fraicheur/date=YYYY-MM-DD/part-0.parquet
  data/bronze/paris_canopee/date=YYYY-MM-DD/part-0.parquet

Schémas Bronze
--------------
airparif_stations :
  station_id        str     Identifiant ARCGIS de la station
  station_name      str
  station_type      str     fond | trafic | industriel
  latitude          float
  longitude         float
  commune_code      str     Code INSEE
  arrondissement    int     1–20 (si Paris)
  polluants         str     JSON list des polluants mesurés
  ingested_at       datetime

paris_ilots_fraicheur :
  site_id           str     Identifiant Paris OD
  nom               str
  categorie         str     jardin | square | bois | ...
  surface_ha        float
  adresse           str
  arrondissement    int
  latitude          float
  longitude         float
  ingested_at       datetime

paris_canopee :
  arbre_id          str
  genre             str     Genre botanique (Platanus, Acer...)
  espece            str
  libelle_francais  str
  hauteur_m         float
  circonference_cm  int
  annee_plantation  int
  arrondissement    int
  latitude          float
  longitude         float
  ingested_at       datetime
"""
from __future__ import annotations

import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .base import build_session, get_logger, save_parquet

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LOG_DIR = Path(__file__).parents[2] / "logs"

# Airparif ArcGIS — Explore API v2.1 (portail Arcgis opendata)
AIRPARIF_ARCGIS_BASE = "https://data-airparif-asso.opendata.arcgis.com/api/explore/v2.1"
AIRPARIF_STATIONS_DATASET = "mesure-en-continu-identification-des-sites-de-mesure"

# Paris Open Data — Explore API v2.1
PARIS_OD_BASE = "https://opendata.paris.fr/api/explore/v2.1/catalog/datasets"
ILOTS_DATASET = "ilots-de-fraicheur-espaces-verts-ouverts-au-public"
ARBRES_DATASET = "les-arbres"

# Limite de récupération des arbres (dataset ~200k lignes — Bronze allégé)
ARBRES_MAX_RECORDS = 50_000

# Pause courtoise entre pages paginées
PAGE_SLEEP_S = 0.15

# ---------------------------------------------------------------------------
# Colonnes Bronze
# ---------------------------------------------------------------------------

COLS_STATIONS = [
    "station_id", "station_name", "station_type",
    "latitude", "longitude", "commune_code", "arrondissement",
    "polluants", "ingested_at",
]
COLS_ILOTS = [
    "site_id", "nom", "categorie", "surface_ha", "adresse",
    "arrondissement", "latitude", "longitude", "ingested_at",
]
COLS_CANOPEE = [
    "arbre_id", "genre", "espece", "libelle_francais",
    "hauteur_m", "circonference_cm", "annee_plantation",
    "arrondissement", "latitude", "longitude", "ingested_at",
]

# Correspondance label arrondissement Paris Open Data → int
_ARR_MAP: dict[str, int] = {
    f"PARIS {i}E ARRDT": i for i in range(2, 21)
}
_ARR_MAP["PARIS 1ER ARRDT"] = 1


def _parse_arrondissement(label: str | None) -> int | None:
    if not label:
        return None
    return _ARR_MAP.get(str(label).strip().upper())


def _extract_coords(geo_point: Any) -> tuple[float | None, float | None]:
    """Extrait lat/lon depuis geo_point_2d (dict {'lat': x, 'lon': y} ou liste [lat, lon])."""
    if isinstance(geo_point, dict):
        return geo_point.get("lat"), geo_point.get("lon")
    if isinstance(geo_point, (list, tuple)) and len(geo_point) >= 2:
        return geo_point[0], geo_point[1]
    return None, None


# ---------------------------------------------------------------------------
# Paginateur générique Paris Open Data / ArcGIS Explore API
# ---------------------------------------------------------------------------

def _paginate_explore(
    session: Any,
    logger: Any,
    url: str,
    params: dict,
    max_records: int = 10_000,
    page_size: int = 100,
) -> list[dict]:
    """
    Pagine l'API Explore v2.1 (data.gouv / Paris OD / ArcGIS).
    Retourne tous les records jusqu'à max_records.
    """
    records: list[dict] = []
    offset = 0
    params = {**params, "limit": page_size}

    while len(records) < max_records:
        params["offset"] = offset
        try:
            resp = session.get(url, params=params)
            if resp.status_code == 429:
                retry = int(resp.headers.get("Retry-After", 5))
                logger.warning("Rate-limit → attente %ds", retry)
                time.sleep(retry)
                continue
            if resp.status_code != 200:
                logger.warning("HTTP %d sur %s — arrêt pagination", resp.status_code, url)
                break

            data = resp.json()
            page = data.get("results", data.get("features", []))
            if not page:
                break

            records.extend(page)
            total = data.get("total_count", data.get("nhits", len(records)))
            logger.debug("  %d / %d records", len(records), min(total, max_records))

            if len(records) >= total or len(records) >= max_records:
                break

            offset += page_size
            time.sleep(PAGE_SLEEP_S)

        except Exception as exc:
            logger.error("Erreur pagination offset=%d : %s", offset, exc)
            break

    return records[:max_records]


# ---------------------------------------------------------------------------
# Source 1 — Airparif ArcGIS : stations de mesure
# ---------------------------------------------------------------------------

def _fetch_airparif_stations(
    session: Any, logger: Any, ingested_at: datetime
) -> pd.DataFrame:
    """Récupère les stations de mesure Airparif depuis leur portail ArcGIS."""
    import json

    url = f"{AIRPARIF_ARCGIS_BASE}/catalog/datasets/{AIRPARIF_STATIONS_DATASET}/records"
    logger.info("Airparif stations → %s", url)

    records = _paginate_explore(
        session, logger, url,
        params={"timezone": "UTC"},
        max_records=500,
        page_size=100,
    )

    if not records:
        # Fallback : essai du endpoint GeoJSON direct
        logger.warning("API ArcGIS vide — tentative GeoJSON fallback")
        geojson_url = (
            "https://data-airparif-asso.opendata.arcgis.com/datasets/"
            "airparif::mesure-en-continu-identification-des-sites-de-mesure.geojson"
        )
        try:
            resp = session.get(geojson_url)
            if resp.status_code == 200:
                features = resp.json().get("features", [])
                records = [f.get("properties", {}) | {"geometry": f.get("geometry", {})}
                           for f in features]
        except Exception as exc:
            logger.error("Fallback GeoJSON échoué : %s", exc)

    if not records:
        logger.warning("Airparif stations : aucune donnée récupérée")
        return pd.DataFrame(columns=COLS_STATIONS)

    rows = []
    for r in records:
        # Extraction géométrie (geometry ou geo_point_2d selon le format)
        geom = r.get("geometry", {})
        geo_pt = r.get("geo_point_2d")
        if geom and geom.get("type") == "Point":
            lon, lat = geom.get("coordinates", [None, None])[:2]
        elif geo_pt:
            lat, lon = _extract_coords(geo_pt)
        else:
            lat, lon = None, None

        # Code INSEE et arrondissement
        commune_code = str(r.get("code_insee", r.get("commune_insee", "")) or "")
        arr = None
        if commune_code.startswith("751") and len(commune_code) == 5:
            try:
                arr = int(commune_code[-2:])
            except ValueError:
                arr = None

        # Liste des polluants
        polluants_raw = r.get("polluants", r.get("Liste_polluants", r.get("liste_polluants", [])))
        if isinstance(polluants_raw, list):
            polluants = json.dumps(polluants_raw, ensure_ascii=False)
        else:
            polluants = str(polluants_raw) if polluants_raw else "[]"

        rows.append({
            "station_id":    str(r.get("id_site", r.get("code_site", r.get("objectid", "")))),
            "station_name":  str(r.get("nom_site", r.get("nom", r.get("libelle", "")))),
            "station_type":  str(r.get("type_de_site", r.get("type_site", "inconnu"))).lower(),
            "latitude":      float(lat) if lat is not None else float("nan"),
            "longitude":     float(lon) if lon is not None else float("nan"),
            "commune_code":  commune_code,
            "arrondissement": arr,
            "polluants":     polluants,
            "ingested_at":   ingested_at,
        })

    df = pd.DataFrame(rows, columns=COLS_STATIONS) if rows else pd.DataFrame(columns=COLS_STATIONS)
    logger.info("Airparif stations → %d stations", len(df))
    return df


# ---------------------------------------------------------------------------
# Source 2 — Paris Open Data : Îlots de fraîcheur
# ---------------------------------------------------------------------------

def _fetch_ilots_fraicheur(
    session: Any, logger: Any, ingested_at: datetime
) -> pd.DataFrame:
    """Récupère les espaces verts / îlots de fraîcheur de Paris Open Data."""
    url = f"{PARIS_OD_BASE}/{ILOTS_DATASET}/records"
    logger.info("Paris Open Data : îlots de fraîcheur → %s", url)

    records = _paginate_explore(
        session, logger, url,
        params={"timezone": "UTC"},
        max_records=5_000,
        page_size=100,
    )

    if not records:
        logger.warning("Îlots de fraîcheur : aucune donnée reçue")
        return pd.DataFrame(columns=COLS_ILOTS)

    rows = []
    for r in records:
        geo_pt = r.get("geo_point_2d") or r.get("geometry_point") or {}
        lat, lon = _extract_coords(geo_pt)

        arr_raw = r.get("arrondissement", r.get("arr", ""))
        arr = _parse_arrondissement(str(arr_raw)) if arr_raw else None

        # Fallback arrondissement depuis le code postal ou l'adresse
        if arr is None:
            cp = str(r.get("code_postal", "") or "")
            if cp.startswith("750") and len(cp) == 5:
                try:
                    arr = int(cp[3:]) or None
                except ValueError:
                    pass

        surface_raw = r.get("surface", r.get("surface_ha", r.get("hectares", None)))
        try:
            surface_ha = float(surface_raw) if surface_raw is not None else float("nan")
        except (ValueError, TypeError):
            surface_ha = float("nan")

        rows.append({
            "site_id":       str(r.get("id_zone", r.get("objectid", r.get("identifiant", "")))),
            "nom":           str(r.get("nom", r.get("libelle", ""))),
            "categorie":     str(r.get("type_et_libertes", r.get("categorie", r.get("type", "")))).lower(),
            "surface_ha":    surface_ha,
            "adresse":       str(r.get("adresse_complete", r.get("adresse", ""))),
            "arrondissement": arr,
            "latitude":      float(lat) if lat is not None else float("nan"),
            "longitude":     float(lon) if lon is not None else float("nan"),
            "ingested_at":   ingested_at,
        })

    df = pd.DataFrame(rows, columns=COLS_ILOTS) if rows else pd.DataFrame(columns=COLS_ILOTS)
    logger.info("Îlots de fraîcheur → %d sites", len(df))
    return df


# ---------------------------------------------------------------------------
# Source 3 — Paris Open Data : Arbres (canopée)
# ---------------------------------------------------------------------------

def _fetch_canopee(
    session: Any, logger: Any, ingested_at: datetime
) -> pd.DataFrame:
    """
    Récupère le référentiel des arbres de Paris (canopée).
    Limité à ARBRES_MAX_RECORDS pour garder un Bronze gérable.
    """
    url = f"{PARIS_OD_BASE}/{ARBRES_DATASET}/records"
    logger.info("Paris Open Data : arbres (canopée) → %s — max %d", url, ARBRES_MAX_RECORDS)

    records = _paginate_explore(
        session, logger, url,
        params={"timezone": "UTC"},
        max_records=ARBRES_MAX_RECORDS,
        page_size=100,
    )

    if not records:
        logger.warning("Arbres : aucune donnée reçue")
        return pd.DataFrame(columns=COLS_CANOPEE)

    rows = []
    for r in records:
        geo_pt = r.get("geo_point_2d") or r.get("geolocalisation") or {}
        lat, lon = _extract_coords(geo_pt)

        arr_raw = r.get("arrondissement", "")
        arr = _parse_arrondissement(str(arr_raw)) if arr_raw else None

        try:
            hauteur = float(r.get("hauteurenm") or r.get("hauteur_en_m") or float("nan"))
        except (ValueError, TypeError):
            hauteur = float("nan")

        try:
            circonf = int(r.get("circonferenceencm") or r.get("circonf_en_cm") or 0)
        except (ValueError, TypeError):
            circonf = 0

        try:
            annee = int(r.get("anneedeplantation") or r.get("annee_plantation") or 0) or None
        except (ValueError, TypeError):
            annee = None

        rows.append({
            "arbre_id":        str(r.get("idbase", r.get("id", ""))),
            "genre":           str(r.get("genre", "")),
            "espece":          str(r.get("espece", r.get("varieteoucultivar", ""))),
            "libelle_francais": str(r.get("libellefrancais", r.get("libelle_francais", ""))),
            "hauteur_m":       hauteur,
            "circonference_cm": circonf,
            "annee_plantation": annee,
            "arrondissement":  arr,
            "latitude":        float(lat) if lat is not None else float("nan"),
            "longitude":       float(lon) if lon is not None else float("nan"),
            "ingested_at":     ingested_at,
        })

    df = pd.DataFrame(rows, columns=COLS_CANOPEE) if rows else pd.DataFrame(columns=COLS_CANOPEE)
    logger.info("Arbres (canopée) → %d arbres (sur %d max demandés)", len(df), ARBRES_MAX_RECORDS)
    return df


# ---------------------------------------------------------------------------
# Point d'entrée public
# ---------------------------------------------------------------------------

def ingest() -> pd.DataFrame:
    """
    Ingère les trois sources de l'indicateur Santé Environnementale.

    Sauvegarde :
      data/bronze/airparif_stations/date=<date>/part-0.parquet
      data/bronze/paris_ilots_fraicheur/date=<date>/part-0.parquet
      data/bronze/paris_canopee/date=<date>/part-0.parquet

    Retourne
    --------
    pd.DataFrame
        DataFrame paris_ilots_fraicheur (source de croisement principale).
    """
    logger = get_logger("health_environment", LOG_DIR)
    ingested_at = datetime.now(timezone.utc)
    run_date = date.today().isoformat()

    logger.info("=" * 60)
    logger.info("Santé Environnementale — ingestion Bronze (%s)", run_date)
    logger.info("=" * 60)

    session = build_session(retries=3, backoff_factor=1.0, timeout=30)

    # --- Source 1 : Airparif stations ---
    logger.info(">>> Source 1/3 : Airparif ArcGIS — stations de mesure")
    df_stations = _fetch_airparif_stations(session, logger, ingested_at)
    if not df_stations.empty:
        path = save_parquet(df_stations, "airparif_stations",
                            partition_col="date", partition_value=run_date,
                            filename="part-0.parquet")
        logger.info("Airparif stations → %d lignes : %s", len(df_stations), path)
    else:
        logger.warning("Airparif stations : aucune donnée — Bronze non créé")

    # --- Source 2 : Îlots de fraîcheur ---
    logger.info(">>> Source 2/3 : Paris Open Data — Îlots de fraîcheur")
    df_ilots = _fetch_ilots_fraicheur(session, logger, ingested_at)
    if not df_ilots.empty:
        path = save_parquet(df_ilots, "paris_ilots_fraicheur",
                            partition_col="date", partition_value=run_date,
                            filename="part-0.parquet")
        logger.info("Îlots de fraîcheur → %d sites : %s", len(df_ilots), path)
    else:
        logger.warning("Îlots de fraîcheur : aucune donnée — Bronze non créé")

    # --- Source 3 : Canopée (arbres) ---
    logger.info(">>> Source 3/3 : Paris Open Data — Arbres (canopée)")
    df_canopee = _fetch_canopee(session, logger, ingested_at)
    if not df_canopee.empty:
        path = save_parquet(df_canopee, "paris_canopee",
                            partition_col="date", partition_value=run_date,
                            filename="part-0.parquet")
        logger.info("Canopée → %d arbres : %s", len(df_canopee), path)
    else:
        logger.warning("Canopée : aucune donnée — Bronze non créé")

    logger.info("Santé Environnementale — ingestion terminée")
    return df_ilots


if __name__ == "__main__":
    result = ingest()
    if not result.empty:
        print("\n--- Aperçu Îlots de fraîcheur (Bronze) ---")
        print(result.head(10).to_string(index=False))
        print(f"\nShape : {result.shape}")
        print(f"Catégories : {sorted(result['categorie'].dropna().unique())}")
