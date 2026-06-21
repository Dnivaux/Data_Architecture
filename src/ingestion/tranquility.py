"""
Tranquillité vs Dynamisme — Bronze Ingestion
=============================================
Sources :
  1. Bruitparif — Indicateurs CBS (Cartes de Bruit Stratégique) Île-de-France
     https://www.bruitparif.fr/opendata-air-bruit/
     Découverte via data.gouv.fr : organisation Bruitparif
  2. SSMSI / data.gouv.fr — Délinquance par commune (réf. vers crime.py existant)
     NB : l'ingestion SSMSI est déléguée au module `crime.py` déjà opérationnel.
          Le présent module charge les données Bronze crime pour les exporter
          dans le contexte tranquillité, sans double-ingestion.

Valeur métier
-------------
Oppose les nuisances (bruit CBS, délinquance) à la densité des bars/boîtes de nuit
(données OSM déjà disponibles) pour scorer la tranquillité / dynamisme nocturne
de chaque arrondissement parisien.

Architecture Bronze (Medallion)
--------------------------------
  data/bronze/bruitparif/date=YYYY-MM-DD/part-0.parquet
     → Indicateurs CBS par commune × source sonore × indicateur
  data/bronze/crime/  (déjà géré par crime.py, lu ici sans re-téléchargement)

Schéma Bronze — bruitparif
--------------------------
  commune_code       str     Code INSEE (ex : '75114')
  arrondissement     int     1–20 pour Paris
  source_bruit       str     routier | ferroviaire | aerien | industrie | total
  indicateur         str     Lden | Ln
  tranche_db         str     55-59 | 60-64 | 65-69 | 70-74 | 75+
  surface_ha         float   Surface exposée (ha)
  pop_exposee        int     Population exposée estimée
  pct_pop_exposee    float   % population communale exposée
  annee_ref          int     Année de référence du CBS
  ingested_at        datetime
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import urllib3
import pandas as pd

# Désactive temporairement les avertissements SSL sur data.gouv.fr
# (certificat intermédiaire manquant côté serveur — contournement en attendant le fix upstream)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from .base import BRONZE_ROOT, build_session, get_logger, read_parquet, save_parquet

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LOG_DIR = Path(__file__).parents[2] / "logs"

DATAGOUV_API = "https://www.data.gouv.fr/api/1/"
DATAGOUV_ORG_BRUITPARIF = "bruitparif"

# Fallback : fichier CBS tabulaire Bruitparif sur data.gouv.fr
# (Indicateurs CBS grands axes routiers IDF — commune level)
BRUITPARIF_FALLBACK_URLS = [
    # CBS routier IDF — le plus complet pour Paris
    "https://www.data.gouv.fr/fr/datasets/r/d44e3eff-0bd5-4f31-9e7d-02c33a2d3d02",
    # CBS ferroviaire IDF
    "https://www.data.gouv.fr/fr/datasets/r/c4e90c6c-7c5b-4f8a-a1a7-73b2e8e4e6c2",
]

# ArcGIS REST API Bruitparif (portail alternatif)
BRUITPARIF_ARCGIS_BASE = "https://bruitparif.opendata.arcgis.com/api/explore/v2.1"
BRUITPARIF_CBS_DATASET = "indicateurs-cbs-par-commune-ile-de-france"

# Source primaire fiable : Paris Open Data — exposition au bruit routier par
# arrondissement (Lden/Ln au-dessus des seuils réglementaire et OMS), par année.
# Format large : 1 colonne par arrondissement (lden_exposition_oms_5eme … _20eme,
# + _pariscentre pour les 1er-4e fusionnés depuis 2020).
PARIS_OD_BASE = "https://opendata.paris.fr/api/explore/v2.1/catalog/datasets"
PARIS_OD_NOISE_DATASET = "bruit-exposition-des-parisien-ne-s-aux-depassements-des-seuils-nocturne-ou-journ"

PARIS_CODES = {f"751{str(i).zfill(2)}" for i in range(1, 21)}

# ---------------------------------------------------------------------------
# Colonnes Bronze Bruitparif
# ---------------------------------------------------------------------------

COLS_BRUIT = [
    "commune_code", "arrondissement", "source_bruit",
    "indicateur", "tranche_db", "surface_ha", "pop_exposee",
    "pct_pop_exposee", "annee_ref", "ingested_at",
]

# Normalisation des sources sonores
_SOURCE_ALIASES: dict[str, str] = {
    "route":        "routier",
    "routier":      "routier",
    "road":         "routier",
    "rail":         "ferroviaire",
    "fer":          "ferroviaire",
    "ferroviaire":  "ferroviaire",
    "train":        "ferroviaire",
    "rer":          "ferroviaire",
    "aérien":       "aerien",
    "aerien":       "aerien",
    "air":          "aerien",
    "aviation":     "aerien",
    "industrie":    "industrie",
    "industriel":   "industrie",
    "total":        "total",
}

# Tranches décibels reconnues (format harmonisé)
_TRANCHES_DB = ["55-59", "60-64", "65-69", "70-74", "75+"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise_source(raw: str) -> str:
    return _SOURCE_ALIASES.get(raw.lower().strip(), raw.lower().strip())


def _bytes_to_df(raw: bytes, logger: Any) -> pd.DataFrame:
    """Décompresse ZIP si nécessaire puis parse CSV, JSON ou GeoJSON."""
    # ZIP
    if raw[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                names = zf.namelist()
                csvs = [n for n in names if n.lower().endswith(".csv")]
                jsons = [n for n in names if n.lower().endswith((".json", ".geojson"))]
                if csvs:
                    with zf.open(csvs[0]) as f:
                        for enc in ("utf-8", "latin-1", "utf-8-sig"):
                            try:
                                return pd.read_csv(f, sep=None, engine="python",
                                                   encoding=enc, low_memory=False)
                            except UnicodeDecodeError:
                                f.seek(0)
                if jsons:
                    with zf.open(jsons[0]) as f:
                        return _json_to_df(json.load(f))
        except zipfile.BadZipFile:
            pass

    # Parquet
    try:
        return pd.read_parquet(io.BytesIO(raw), engine="pyarrow")
    except Exception:
        pass

    # JSON / GeoJSON
    try:
        data = json.loads(raw)
        return _json_to_df(data)
    except Exception:
        pass

    # CSV
    for sep in (";", ",", "\t"):
        for enc in ("utf-8", "latin-1", "utf-8-sig"):
            try:
                return pd.read_csv(io.BytesIO(raw), sep=sep, encoding=enc, low_memory=False)
            except Exception:
                continue

    logger.error("Format de fichier Bruitparif non reconnu")
    return pd.DataFrame()


def _json_to_df(data: Any) -> pd.DataFrame:
    """Convertit GeoJSON FeatureCollection ou JSON list en DataFrame."""
    if isinstance(data, dict) and data.get("type") == "FeatureCollection":
        rows = []
        for feat in data.get("features", []):
            row = feat.get("properties", {})
            geom = feat.get("geometry", {})
            coords = geom.get("coordinates") if geom else None
            if coords and isinstance(coords, (list, tuple)) and len(coords) >= 2:
                row["_lon"], row["_lat"] = coords[0], coords[1]
            rows.append(row)
        return pd.DataFrame(rows)
    if isinstance(data, list):
        return pd.DataFrame(data)
    return pd.DataFrame()


def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    cols_lower = {c.lower(): c for c in df.columns}
    for c in candidates:
        if c in df.columns:
            return c
        if c.lower() in cols_lower:
            return cols_lower[c.lower()]
    return None


# ---------------------------------------------------------------------------
# Stratégie de découverte Bruitparif
# ---------------------------------------------------------------------------

def _discover_bruitparif_url(session: Any, logger: Any) -> list[str]:
    """
    Cherche les ressources CBS Bruitparif sur data.gouv.fr.

    Stratégie :
    1. Accès direct au dataset par slug
    2. Recherche par mots-clés "cartes bruit strategiques ile-de-france"
    3. Recherche par mots-clés "bruitparif CBS communes"
    """
    urls: list[str] = []
    FORMATS_OK = ("csv", "zip", "json", "geojson", "xlsx")
    KEYWORDS_CBS = [
        "cartes bruit strategiques grands axes routiers ile-de-france",
        "bruitparif CBS indicateurs communes ile-de-france",
        "exposition bruit ile-de-france communes",
    ]
    CBS_SLUGS = [
        "cartes-de-bruit-strategiques-des-grands-axes-routiers-nationaux-en-ile-de-france",
        "bruit-des-infrastructures-routieres-nationales-en-ile-de-france",
    ]

    # --- Tentative 1 : slugs connus ---
    for slug in CBS_SLUGS:
        try:
            resp = session.get(f"{DATAGOUV_API}{slug}/", timeout=15, verify=False)
            if resp.status_code == 200:
                for res in resp.json().get("resources", []):
                    if res.get("format", "").lower() in FORMATS_OK:
                        urls.append(res["url"])
                        logger.info("Bruitparif slug '%s' → %s", slug, res["url"])
                if urls:
                    return urls
        except Exception as exc:
            logger.debug("Slug Bruitparif '%s' échoué : %s", slug, exc)

    # --- Tentative 2 : recherche par mots-clés ---
    for kw in KEYWORDS_CBS:
        try:
            resp = session.get(
                DATAGOUV_API,
                params={"q": kw, "page_size": 10, "sort": "-created"},
                timeout=15,
                verify=False,
            )
            if resp.status_code == 200:
                for ds in resp.json().get("data", []):
                    title = ds.get("title", "").lower()
                    if any(t in title for t in ("bruit", "cbs", "bruitparif", "sonore")):
                        for res in ds.get("resources", []):
                            if res.get("format", "").lower() in FORMATS_OK:
                                urls.append(res["url"])
                                logger.info("Bruitparif via '%s' : %s", kw[:40], res["url"])
            if urls:
                return urls
        except Exception as exc:
            logger.warning("Recherche Bruitparif '%s' échouée : %s", kw[:40], exc)

    return urls


def _try_arcgis_bruitparif(session: Any, logger: Any, ingested_at: datetime) -> pd.DataFrame:
    """
    Tentative via le portail ArcGIS de Bruitparif (alternative si data.gouv.fr vide).
    Retourne un DataFrame normalisé ou vide.
    """
    import time

    url = f"{BRUITPARIF_ARCGIS_BASE}/catalog/datasets/{BRUITPARIF_CBS_DATASET}/records"
    logger.info("Bruitparif ArcGIS → %s", url)

    records: list[dict] = []
    offset = 0
    while True:
        try:
            resp = session.get(url, params={"limit": 100, "offset": offset, "timezone": "UTC"})
            if resp.status_code == 404:
                logger.warning("Dataset ArcGIS Bruitparif introuvable (404)")
                break
            if resp.status_code != 200:
                break
            data = resp.json()
            page = data.get("results", [])
            if not page:
                break
            records.extend(page)
            if len(records) >= data.get("total_count", len(records)):
                break
            offset += 100
            time.sleep(0.2)
        except Exception:
            break

    if not records:
        return pd.DataFrame(columns=COLS_BRUIT)

    return _normalise_bruitparif_records(records, ingested_at)


def _normalise_bruitparif_records(records: list[dict], ingested_at: datetime) -> pd.DataFrame:
    """Normalise une liste de records Bruitparif (quel que soit le format source)."""
    rows = []

    for r in records:
        # Code commune
        code_raw = str(
            r.get("code_insee", r.get("code_commune", r.get("CODGEO", r.get("insee", "")))) or ""
        ).strip()

        # Arrondissement
        arr = None
        if code_raw in PARIS_CODES:
            try:
                arr = int(code_raw[-2:])
            except ValueError:
                pass

        # Source sonore
        src_raw = str(
            r.get("source_bruit", r.get("source", r.get("type_source", r.get("type", "")))) or ""
        )
        source = _normalise_source(src_raw) if src_raw else "total"

        # Indicateur (Lden / Ln)
        indic_raw = str(r.get("indicateur", r.get("indice", r.get("type_indicateur", "Lden"))) or "Lden")
        indicateur = "Ln" if "ln" in indic_raw.lower() else "Lden"

        # Tranche décibel
        tranche_raw = str(
            r.get("tranche_db", r.get("tranche", r.get("classe_db", r.get("niveau", "")))) or ""
        ).strip()
        tranche = tranche_raw if tranche_raw in _TRANCHES_DB else "inconnu"

        # Indicateurs quantitatifs
        try:
            surface_ha = float(r.get("surface_ha", r.get("surface", r.get("superficie_ha", 0))) or 0)
        except (ValueError, TypeError):
            surface_ha = float("nan")

        try:
            pop_exposee = int(float(r.get("pop_exposee", r.get("population", r.get("pop", 0))) or 0))
        except (ValueError, TypeError):
            pop_exposee = 0

        try:
            pct = float(r.get("pct_pop_exposee", r.get("pct_pop", r.get("pourcentage_pop", 0))) or 0)
        except (ValueError, TypeError):
            pct = float("nan")

        try:
            annee = int(r.get("annee_ref", r.get("annee", r.get("year", 0))) or 0) or None
        except (ValueError, TypeError):
            annee = None

        rows.append({
            "commune_code":    code_raw,
            "arrondissement":  arr,
            "source_bruit":    source,
            "indicateur":      indicateur,
            "tranche_db":      tranche,
            "surface_ha":      surface_ha,
            "pop_exposee":     pop_exposee,
            "pct_pop_exposee": pct,
            "annee_ref":       annee,
            "ingested_at":     ingested_at,
        })

    df = pd.DataFrame(rows, columns=COLS_BRUIT) if rows else pd.DataFrame(columns=COLS_BRUIT)
    # Filtre Paris uniquement
    df_paris = df[df["commune_code"].isin(PARIS_CODES)].copy()
    return df_paris.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Source primaire — Paris Open Data exposition bruit par arrondissement
# ---------------------------------------------------------------------------

def _fetch_paris_noise(session: Any, logger: Any, ingested_at: datetime) -> pd.DataFrame:
    """
    Récupère l'exposition au bruit routier par arrondissement (Paris Open Data).

    Le dataset est en format large (1 colonne par arrondissement). On prend
    l'année la plus récente et on déplie en lignes longues compatibles avec
    le schéma Bronze bruitparif (commune_code, indicateur Lden/Ln, surface_ha).

    On retient le seuil OMS (~53 dB Lden / 45 dB Ln), sémantiquement proche de
    « surface/population exposée ≥ 55 dB » utilisé par le score Tranquillité.
    Paris Centre (colonne *_pariscentre*) couvre les 1er-4e : la valeur est
    répartie également sur ces 4 arrondissements.
    """
    url = f"{PARIS_OD_BASE}/{PARIS_OD_NOISE_DATASET}/records"
    try:
        resp = session.get(url, params={"limit": 100, "order_by": "annee desc"}, verify=False)
        if resp.status_code != 200:
            logger.warning("Paris OD bruit HTTP %d", resp.status_code)
            return pd.DataFrame(columns=COLS_BRUIT)
        results = resp.json().get("results", [])
    except Exception as exc:
        logger.warning("Paris OD bruit échoué : %s", exc)
        return pd.DataFrame(columns=COLS_BRUIT)

    if not results:
        return pd.DataFrame(columns=COLS_BRUIT)

    def _year(r: dict) -> int:
        try:
            return int(r.get("annee") or 0)
        except (ValueError, TypeError):
            return 0

    latest = max(results, key=_year)
    annee = _year(latest)

    rows: list[dict] = []
    for arr in range(1, 21):
        suffix = "pariscentre" if arr <= 4 else f"{arr}eme"
        divisor = 4.0 if arr <= 4 else 1.0
        for indic in ("Lden", "Ln"):
            key = f"{indic.lower()}_exposition_oms_{suffix}"
            val = latest.get(key)
            try:
                surface = float(val) / divisor if val is not None else float("nan")
            except (ValueError, TypeError):
                surface = float("nan")
            rows.append({
                "commune_code":    f"751{arr:02d}",
                "arrondissement":  arr,
                "source_bruit":    "routier",
                "indicateur":      indic,
                "tranche_db":      "55+",
                "surface_ha":      surface,
                "pop_exposee":     0,
                "pct_pop_exposee": float("nan"),
                "annee_ref":       annee,
                "ingested_at":     ingested_at,
            })

    df = pd.DataFrame(rows, columns=COLS_BRUIT)
    logger.info("Paris OD bruit → %d lignes (année %d, 20 arrondissements)", len(df), annee)
    return df


# ---------------------------------------------------------------------------
# Récupération principale Bruitparif
# ---------------------------------------------------------------------------

def _fetch_bruitparif(session: Any, logger: Any, ingested_at: datetime) -> pd.DataFrame:
    """
    Orchestration multi-tentatives pour Bruitparif :
    1. Découverte dynamique via data.gouv.fr
    2. URLs de fallback connues
    3. Portail ArcGIS Bruitparif
    """
    # --- Tentative 1 : découverte data.gouv.fr ---
    discovered_urls = _discover_bruitparif_url(session, logger)
    all_urls = discovered_urls + BRUITPARIF_FALLBACK_URLS

    for url in all_urls:
        logger.info("Bruitparif → essai : %s", url)
        try:
            resp = session.get(url, verify=False)
            if resp.status_code != 200:
                logger.warning("HTTP %d", resp.status_code)
                continue

            df_raw = _bytes_to_df(resp.content, logger)
            if df_raw.empty:
                logger.warning("Fichier vide ou non parsable")
                continue

            logger.info("Bruitparif brut : %d × %d", *df_raw.shape)

            # Filtrer Paris si possible
            code_col = _find_col(df_raw, ["code_insee", "code_commune", "CODGEO", "insee"])
            if code_col:
                df_paris = df_raw[df_raw[code_col].astype(str).str.strip().isin(PARIS_CODES)].copy()
                if df_paris.empty:
                    logger.debug("Aucun arrondissement parisien dans ce fichier")
                    continue
                records = df_paris.to_dict("records")
            else:
                records = df_raw.to_dict("records")

            df = _normalise_bruitparif_records(records, ingested_at)
            if not df.empty:
                logger.info("Bruitparif Paris → %d lignes (source : %s)", len(df), url)
                return df

        except Exception as exc:
            logger.warning("Erreur sur %s : %s", url, exc)

    # --- Tentative 2 : portail ArcGIS ---
    logger.info("Tentative ArcGIS Bruitparif...")
    df_arcgis = _try_arcgis_bruitparif(session, logger, ingested_at)
    if not df_arcgis.empty:
        return df_arcgis

    logger.warning(
        "Bruitparif : toutes les sources ont échoué. "
        "Vérifier la disponibilité de l'OpenData sur https://www.bruitparif.fr/opendata-air-bruit/"
    )
    return pd.DataFrame(columns=COLS_BRUIT)


# ---------------------------------------------------------------------------
# Lecture des données crime Bronze existantes
# ---------------------------------------------------------------------------

def _load_crime_bronze(logger: Any) -> pd.DataFrame:
    """
    Charge les données SSMSI déjà ingérées par crime.py depuis le Bronze.
    Retourne un DataFrame vide si crime.py n'a pas encore tourné.
    """
    df = read_parquet("crime")
    if df.empty:
        logger.warning(
            "Bronze crime vide — exécuter d'abord `python main.py --sources crime` "
            "ou `from src.ingestion.crime import ingest; ingest()`"
        )
    else:
        logger.info("Bronze crime chargé : %d lignes (SSMSI déjà ingéré)", len(df))
    return df


# ---------------------------------------------------------------------------
# Point d'entrée public
# ---------------------------------------------------------------------------

def ingest() -> pd.DataFrame:
    """
    Ingère les données de l'indicateur Tranquillité vs Dynamisme.

    Stratégie :
      - Bruitparif CBS → téléchargement + normalisation → Bronze
      - SSMSI crime → lecture du Bronze existant (crime.py) sans re-téléchargement

    Sauvegarde :
      data/bronze/bruitparif/date=<date>/part-0.parquet

    Retourne
    --------
    pd.DataFrame
        DataFrame Bruitparif (source principale de ce module).
    """
    logger = get_logger("tranquility", LOG_DIR)
    ingested_at = datetime.now(timezone.utc)
    run_date = date.today().isoformat()

    logger.info("=" * 60)
    logger.info("Tranquillité vs Dynamisme — ingestion Bronze (%s)", run_date)
    logger.info("=" * 60)

    session = build_session(retries=3, backoff_factor=2.0, timeout=60)

    # --- Source 1 : exposition bruit (Paris OD primaire, Bruitparif CBS fallback) ---
    logger.info(">>> Source 1/2 : Exposition au bruit par arrondissement")
    df_bruit = _fetch_paris_noise(session, logger, ingested_at)
    if df_bruit.empty:
        logger.info("Paris OD bruit indisponible — repli sur Bruitparif CBS")
        df_bruit = _fetch_bruitparif(session, logger, ingested_at)
    if not df_bruit.empty:
        path = save_parquet(
            df_bruit, "bruitparif",
            partition_col="date", partition_value=run_date,
            filename="part-0.parquet",
        )
        logger.info("Bruitparif → %d lignes sauvegardées : %s", len(df_bruit), path)
    else:
        logger.warning("Bruitparif : aucune donnée disponible — Bronze non créé")

    # --- Source 2 : SSMSI crime (lecture Bronze existant) ---
    logger.info(">>> Source 2/2 : SSMSI — chargement Bronze crime existant")
    df_crime = _load_crime_bronze(logger)
    # Le DataFrame crime n'est PAS ré-écrit ici — crime.py en est propriétaire.
    # En couche Silver, tranquillité = croisement bruitparif + crime + osm(bars/nightclub).

    logger.info(
        "Tranquillité vs Dynamisme — ingestion terminée "
        "(bruitparif=%d lignes, crime=%d lignes disponibles)",
        len(df_bruit) if not df_bruit.empty else 0,
        len(df_crime),
    )
    return df_bruit


if __name__ == "__main__":
    result = ingest()
    if not result.empty:
        print("\n--- Aperçu Bruitparif (Bronze) ---")
        print(result.head(15).to_string(index=False))
        print(f"\nShape         : {result.shape}")
        print(f"Sources bruit : {sorted(result['source_bruit'].unique())}")
        print(f"Indicateurs   : {sorted(result['indicateur'].unique())}")
        print(f"Tranches dB   : {sorted(result['tranche_db'].unique())}")
    else:
        print("Aucune donnée Bruitparif disponible.")
        print("Conseil : vérifier https://www.bruitparif.fr/opendata-air-bruit/")
        print("          et ajouter la bonne URL dans BRUITPARIF_FALLBACK_URLS")
