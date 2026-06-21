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

import urllib3
import pandas as pd

# Désactive les avertissements SSL (certificat intermédiaire manquant sur data.gouv.fr)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from .base import build_session, get_logger, save_parquet

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LOG_DIR = Path(__file__).parents[2] / "logs"

# data.gouv.fr — Airparif Indices Citeair journaliers par commune IDF
# Dataset : "Indices Qualité de l'air (Citeair) journaliers par polluant …"
# Organisation Airparif (id: 5a4381adc751df74bae4627d)
# Dataset id : 5a4651eb88ee380bb9eff81e
DATAGOUV_API         = "https://www.data.gouv.fr/api/1/datasets/"
AIRPARIF_DATASET_ID  = "5a4651eb88ee380bb9eff81e"
AIRPARIF_ORG_ID      = "5a4381adc751df74bae4627d"

# Paris Open Data — Explore API v2.1
PARIS_OD_BASE = "https://opendata.paris.fr/api/explore/v2.1/catalog/datasets"
PARIS_OD_CATALOG = "https://opendata.paris.fr/api/explore/v2.1/catalog/datasets"

# Codes INSEE Paris
_PARIS_CODES = {f"751{str(i).zfill(2)}" for i in range(1, 21)}
ILOTS_DATASET = "ilots-de-fraicheur-espaces-verts-ouverts-au-public"
ARBRES_DATASET = "les-arbres"

# Limite de récupération des arbres (dataset ~200k lignes — Bronze allégé)
ARBRES_MAX_RECORDS = 50_000

# Pause courtoise entre pages paginées
PAGE_SLEEP_S = 0.15

# ---------------------------------------------------------------------------
# Colonnes Bronze
# ---------------------------------------------------------------------------

# Citeair : sous-indices NO2, O3, PM10 + indice global max + label
COLS_ATMO = [
    "commune_code", "arrondissement", "date_ref",
    "no2_index", "o3_index", "pm10_index",
    "indice_atmo_num", "indice_atmo_label",
    "ingested_at",
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


def _search_paris_od_dataset(session: Any, logger: Any, keywords: str) -> str | None:
    """
    Recherche dynamique d'un dataset sur le catalogue Paris Open Data.
    Retourne le dataset_id si trouvé, None sinon.
    """
    try:
        resp = session.get(
            PARIS_OD_CATALOG,
            params={"q": keywords, "limit": 5, "timezone": "UTC"},
            timeout=15,
        )
        if resp.status_code == 200:
            for ds in resp.json().get("datasets", []):
                dataset_id = ds.get("dataset", {}).get("dataset_id", "")
                if dataset_id:
                    logger.info("Dataset Paris OD trouvé via recherche '%s' : %s", keywords, dataset_id)
                    return dataset_id
    except Exception as exc:
        logger.warning("Recherche Paris OD '%s' échouée : %s", keywords, exc)
    return None


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
# Source 1 — Airparif : Indices Citeair journaliers par arrondissement
# ---------------------------------------------------------------------------

# Seuils Citeair (sous-indices 0–100+) → label lisible
_CITEAIR_LABELS = [
    (0,  25,  "très bon"),
    (25, 50,  "bon"),
    (50, 75,  "moyen"),
    (75, 100, "mauvais"),
    (100, float("inf"), "très mauvais"),
]


def _citeair_label(index: float) -> str:
    for lo, hi, label in _CITEAIR_LABELS:
        if lo <= index < hi:
            return label
    return "inconnu"


def _fetch_airparif_atmo(
    session: Any, logger: Any, ingested_at: datetime
) -> pd.DataFrame:
    """
    Télécharge les indices Citeair journaliers Airparif depuis data.gouv.fr.

    Dataset : "Indices Qualité de l'air (Citeair) journaliers par polluant …"
    ID      : 5a4651eb88ee380bb9eff81e (Airparif, données 2014-2018)

    Stratégie :
    1. Récupère la liste des ressources CSV via l'API data.gouv.fr.
    2. Télécharge tous les CSV disponibles (une archive par mois/an).
    3. Filtre pour les arrondissements parisiens (ninsee 75101-75120).
    4. Calcule l'indice Citeair global = max(no2, o3, pm10) par ligne.
    5. Retourne le DataFrame complet (toutes dates × tous arrondissements).

    Schéma Bronze sortant : COLS_ATMO
      commune_code, arrondissement, date_ref,
      no2_index, o3_index, pm10_index, indice_atmo_num, indice_atmo_label,
      ingested_at
    """
    import io

    PARIS_CODES = {f"751{str(i).zfill(2)}" for i in range(1, 21)}

    # --- Étape 1 : récupérer les URLs des ressources CSV ---
    logger.info("Airparif ATMO → lecture dataset data.gouv.fr (id=%s)", AIRPARIF_DATASET_ID)
    csv_urls: list[str] = []
    try:
        resp = session.get(
            f"{DATAGOUV_API}{AIRPARIF_DATASET_ID}/",
            timeout=15,
            verify=False,
        )
        if resp.status_code != 200:
            logger.warning("data.gouv.fr dataset HTTP %d — fallback recherche par org", resp.status_code)
            raise ValueError(f"HTTP {resp.status_code}")
        for res in resp.json().get("resources", []):
            if res.get("format", "").lower() == "csv":
                csv_urls.append(res["url"])
        logger.info("Airparif ATMO : %d ressources CSV trouvées", len(csv_urls))
    except Exception as exc:
        logger.warning("Accès direct dataset échoué (%s) — recherche par org", exc)
        # Fallback : recherche dans les datasets de l'organisation Airparif
        try:
            resp = session.get(
                DATAGOUV_API,
                params={"organization": AIRPARIF_ORG_ID, "page_size": 20},
                timeout=15,
                verify=False,
            )
            for ds in resp.json().get("data", []):
                title = ds.get("title", "").lower()
                if "citeair" in title or "indice" in title:
                    for res in ds.get("resources", []):
                        if res.get("format", "").lower() == "csv":
                            csv_urls.append(res["url"])
            logger.info("Fallback org : %d CSV trouvés", len(csv_urls))
        except Exception as exc2:
            logger.warning("Fallback org échoué : %s", exc2)

    if not csv_urls:
        logger.warning(
            "Airparif ATMO : aucune ressource CSV accessible "
            "— pipeline poursuivi sans données qualité air"
        )
        return pd.DataFrame(columns=COLS_ATMO)

    # --- Étape 2 : téléchargement et empilement des CSV ---
    frames: list[pd.DataFrame] = []
    for url in csv_urls:
        logger.debug("Airparif ATMO → téléchargement : %s", url[-60:])
        try:
            r = session.get(url, timeout=60, verify=False)
            if r.status_code != 200:
                logger.warning("HTTP %d pour %s — ignoré", r.status_code, url[-60:])
                continue
            df_raw = pd.read_csv(
                io.BytesIO(r.content),
                sep=",", encoding="utf-8", low_memory=False,
            )
            if not {"ninsee", "no2", "o3", "pm10"}.issubset(df_raw.columns):
                logger.debug("Colonnes attendues absentes dans %s — ignoré", url[-40:])
                continue
            frames.append(df_raw)
        except Exception as exc:
            logger.warning("Erreur téléchargement %s : %s", url[-60:], exc)

    if not frames:
        logger.warning("Airparif ATMO : aucun CSV parsable — pipeline sans données air")
        return pd.DataFrame(columns=COLS_ATMO)

    df_all = pd.concat(frames, ignore_index=True)
    logger.info("Airparif ATMO brut : %d lignes (tous territoires IDF)", len(df_all))

    # --- Étape 3 : filtre Paris arrondissements ---
    df_all["ninsee"] = df_all["ninsee"].astype(str).str.strip().str.zfill(5)
    df_paris = df_all[df_all["ninsee"].isin(PARIS_CODES)].copy()
    logger.info("Airparif ATMO Paris : %d lignes (20 arrondissements)", len(df_paris))

    if df_paris.empty:
        logger.warning("Airparif ATMO : aucun arrondissement parisien dans les données")
        return pd.DataFrame(columns=COLS_ATMO)

    # --- Étape 4 : construction du Bronze normalisé ---
    df_paris["commune_code"] = df_paris["ninsee"]
    df_paris["arrondissement"] = df_paris["ninsee"].str[-2:].astype(int)

    # Parsing de la date DD/MM/YYYY → datetime
    df_paris["date_ref"] = pd.to_datetime(
        df_paris["date"], format="%d/%m/%Y", errors="coerce"
    )

    # Sous-indices numériques
    for col in ["no2", "o3", "pm10"]:
        df_paris[col] = pd.to_numeric(df_paris[col], errors="coerce")

    # Indice Citeair global = max des sous-indices (méthode officielle)
    df_paris["indice_atmo_num"] = df_paris[["no2", "o3", "pm10"]].max(axis=1)
    df_paris["indice_atmo_label"] = df_paris["indice_atmo_num"].apply(
        lambda v: _citeair_label(v) if pd.notna(v) else "inconnu"
    )

    df_out = df_paris[[
        "commune_code", "arrondissement", "date_ref",
        "no2", "o3", "pm10",
        "indice_atmo_num", "indice_atmo_label",
    ]].rename(columns={"no2": "no2_index", "o3": "o3_index", "pm10": "pm10_index"})

    # Suppression des doublons (même commune × même date)
    df_out = df_out.drop_duplicates(subset=["commune_code", "date_ref"])
    df_out["ingested_at"] = ingested_at
    df_out = df_out[COLS_ATMO].reset_index(drop=True)

    logger.info(
        "Airparif ATMO → %d lignes — indice moyen Paris : %.1f (Citeair)",
        len(df_out),
        df_out["indice_atmo_num"].mean(),
    )
    return df_out


# ---------------------------------------------------------------------------
# Source 2 — Paris Open Data : Îlots de fraîcheur
# ---------------------------------------------------------------------------

def _fetch_ilots_fraicheur(
    session: Any, logger: Any, ingested_at: datetime
) -> pd.DataFrame:
    """
    Récupère les espaces verts / îlots de fraîcheur de Paris Open Data.

    Stratégie :
      1. Découverte dynamique du dataset_id via le catalogue Paris Open Data
         (évite les erreurs 404 liées aux changements d'identifiant de dataset)
      2. Extraction paginée des records depuis le dataset trouvé
      3. Normalisation vers le schéma Bronze COLS_ILOTS

    Aucune donnée factice : retourne un DataFrame vide si le dataset est introuvable.
    """
    # ---- 1. Découverte dynamique du dataset_id ----
    dataset_id: str | None = None
    try:
        resp = session.get(
            PARIS_OD_CATALOG,
            params={"q": "ilots fraicheur espaces verts", "limit": 3, "timezone": "UTC"},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            # API v2.1 → clé "results" ; ancienne API → clé "datasets"
            candidates = data.get("results", data.get("datasets", []))
            for ds in candidates:
                # Format v2.1 : dataset_id directement dans le dict
                did = ds.get("dataset_id", "")
                # Format legacy : imbriqué sous la clé "dataset"
                if not did:
                    did = ds.get("dataset", {}).get("dataset_id", "")
                if did:
                    dataset_id = did
                    logger.info(
                        "Îlots de fraîcheur : dataset découvert dynamiquement → '%s'",
                        dataset_id,
                    )
                    break
            if dataset_id is None:
                logger.warning(
                    "Îlots de fraîcheur : recherche catalogue OK (HTTP 200) "
                    "mais aucun dataset_id extrait. Réponse : %s",
                    str(data)[:300],
                )
        else:
            logger.warning(
                "Catalogue Paris Open Data : HTTP %d pour la recherche îlots de fraîcheur",
                resp.status_code,
            )
    except Exception as exc:
        logger.warning("Découverte catalogue Paris Open Data îlots échouée : %s", exc)

    if dataset_id is None:
        logger.warning(
            "Îlots de fraîcheur : dataset introuvable sur Paris Open Data — "
            "Bronze paris_ilots_fraicheur non produit (aucune donnée factice)"
        )
        return pd.DataFrame(columns=COLS_ILOTS)

    # ---- 2. Extraction paginée des records ----
    url = f"{PARIS_OD_BASE}/{dataset_id}/records"
    logger.info("Îlots de fraîcheur : extraction → %s", url)

    records = _paginate_explore(
        session, logger, url,
        params={"timezone": "UTC"},
        max_records=5_000,
        page_size=100,
    )

    if not records:
        logger.warning(
            "Îlots de fraîcheur : aucune donnée reçue depuis le dataset '%s'", dataset_id
        )
        return pd.DataFrame(columns=COLS_ILOTS)

    # ---- 3. Normalisation ----
    rows = []
    for r in records:
        geo_pt = r.get("geo_point_2d") or r.get("geometry_point") or {}
        lat, lon = _extract_coords(geo_pt)

        arr_raw = r.get("arrondissement", r.get("arr", ""))
        arr = _parse_arrondissement(str(arr_raw)) if arr_raw else None

        # Repli arrondissement depuis le code postal (750XX)
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
            "site_id":        str(r.get("id_zone", r.get("objectid", r.get("identifiant", "")))),
            "nom":            str(r.get("nom", r.get("libelle", ""))),
            "categorie":      str(r.get("type_et_libertes", r.get("categorie", r.get("type", "")))).lower(),
            "surface_ha":     surface_ha,
            "adresse":        str(r.get("adresse_complete", r.get("adresse", ""))),
            "arrondissement": arr,
            "latitude":       float(lat) if lat is not None else float("nan"),
            "longitude":      float(lon) if lon is not None else float("nan"),
            "ingested_at":    ingested_at,
        })

    df = pd.DataFrame(rows, columns=COLS_ILOTS) if rows else pd.DataFrame(columns=COLS_ILOTS)
    logger.info("Îlots de fraîcheur → %d sites (dataset '%s')", len(df), dataset_id)
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

    # --- Source 1 : Airparif indices Citeair ---
    logger.info(">>> Source 1/3 : Airparif — Indices Citeair (data.gouv.fr)")
    df_atmo = _fetch_airparif_atmo(session, logger, ingested_at)
    if not df_atmo.empty:
        path = save_parquet(df_atmo, "air_quality",
                            partition_col="date", partition_value=run_date,
                            filename="part-0.parquet")
        logger.info("Airparif ATMO → %d lignes : %s", len(df_atmo), path)
    else:
        logger.warning("Airparif ATMO : aucune donnée — Bronze air_quality non créé")

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
    return df_atmo


if __name__ == "__main__":
    result = ingest()
    if not result.empty:
        print("\n--- Aperçu Îlots de fraîcheur (Bronze) ---")
        print(result.head(10).to_string(index=False))
        print(f"\nShape : {result.shape}")
        print(f"Catégories : {sorted(result['categorie'].dropna().unique())}")
