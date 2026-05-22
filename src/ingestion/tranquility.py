"""
Tranquillité vs Dynamisme — Bronze Ingestion
=============================================
Sources :
  1. data.gouv.fr — "Bruit routier - Exposition des Parisien(ne)s aux dépassements
     des seuils nocturne ou journée complète" (Ville de Paris / Bruitparif)
     Recherche : GET /api/1/datasets/?q=Bruit+routier+-+Exposition+des+Parisien(ne)s&page_size=5
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
     → Indicateurs Lden/Ln par arrondissement (source routière)
  data/bronze/crime/  (déjà géré par crime.py, lu ici sans re-téléchargement)

Schéma Bronze — bruitparif
--------------------------
  commune_code       str     Code INSEE (ex : '75114')
  arrondissement     int     1–20 pour Paris
  source_bruit       str     routier | ferroviaire | aerien | industrie | total
  indicateur         str     Lden | Ln
  tranche_db         str     55-59 | 60-64 | 65-69 | 70-74 | 75+ | ≥55
  surface_ha         float   Surface exposée (ha)
  pop_exposee        int     Population exposée estimée
  pct_pop_exposee    float   % population communale exposée
  annee_ref          int     Année de référence du CBS
  ingested_at        datetime
"""
from __future__ import annotations

import io
import json
import re
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

DATAGOUV_API      = "https://www.data.gouv.fr/api/1/"
DATAGOUV_SEARCH_Q = "Bruit routier - Exposition des Parisien(ne)s"

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
# Helpers génériques
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

    # CSV — plusieurs séparateurs et encodages
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
# Parser du dataset Paris bruit routier
# ---------------------------------------------------------------------------

def _extract_arr_from_colname(col: str) -> list[int]:
    """
    Extrait le(s) numéro(s) d'arrondissement depuis un nom de colonne Bruitparif.

    Format attendu (exemples réels) :
      '... Paris centre ...'  → [1, 2, 3, 4]   (Paris Centre regroupe les 4 premiers)
      '... 5ème arrdt ...'    → [5]
      '... 10ème arrdt ...'   → [10]
      '... 1er arrdt ...'     → [1]
    """
    col_l = col.lower()

    # "Paris centre" = arrondissements 1, 2, 3, 4 regroupés
    if "centre" in col_l:
        return [1, 2, 3, 4]

    # Ordinaux français : "5ème arrdt", "2e arrdt", "1er arrdt", "1ère arrdt"
    m = re.search(
        r"\b(\d{1,2})\s*(?:ème|eme|ière|iere|ier|er|ère|ere|e)\s*"
        r"(?:arrdt|arrondissement|arr\.?)",
        col_l,
    )
    if m:
        n = int(m.group(1))
        return [n] if 1 <= n <= 20 else []

    return []


def _parse_bruitparif_paris(
    df_raw: pd.DataFrame,
    ingested_at: datetime,
    logger: Any,
) -> pd.DataFrame:
    """
    Parse le CSV du dataset 'Bruit routier — Exposition des Parisien(ne)s'.

    Format réel : une colonne par arrondissement × indicateur.
    Exemples de noms de colonnes :
      'Nbre habitants Paris centre soumis à dépassement VR Lden'
      'Nbre habitants 5ème arrdt soumis à dépassement VR Lden'
      'Nbre habitants 10ème arrdt soumis à dépassement VR Ln'

    Stratégie :
      1. Identifier chaque colonne comme Lden ou Ln (mot-clé dans le nom)
      2. Extraire l'arrondissement depuis le nom de la colonne
      3. "Paris centre" → répartir la valeur ÷ 4 sur les arrondissements 1-4
      4. Sommer toutes les lignes (plusieurs lignes possibles si multi-sources)
      5. Construire une ligne par (arrondissement, indicateur) → COLS_BRUIT

    Source bruit = 'routier' (dataset dédié).
    tranche_db   = '≥55'     (seuils réglementaires Lden 55 dB / Ln 50 dB).
    surface_ha et pct_pop_exposee = NaN (non disponibles dans ce dataset).
    """
    from collections import defaultdict

    logger.info("Colonnes brutes (%d) : %s", len(df_raw.columns), list(df_raw.columns))

    # Accumulateur : (arrondissement, indicateur) → pop_exposee cumulée
    pop_acc: dict[tuple[int, str], float] = defaultdict(float)
    matched_cols = 0

    for col in df_raw.columns:
        col_l = col.lower()

        # ---- Identifier l'indicateur depuis le nom de la colonne ----
        if "lden" in col_l:
            indicateur = "Lden"
        elif re.search(r"\bvr\s*ln\b|\bln\b", col_l):
            indicateur = "Ln"
        else:
            continue  # colonne non liée aux indicateurs bruit

        # ---- Extraire le(s) arrondissement(s) depuis le nom ----
        arrs = _extract_arr_from_colname(col)
        if not arrs:
            logger.debug("Arrondissement non détecté dans la colonne : '%s'", col)
            continue

        # Sommer toutes les lignes du CSV (plusieurs lignes = plusieurs sources/routes)
        col_total = pd.to_numeric(df_raw[col], errors="coerce").fillna(0).sum()

        if len(arrs) > 1:
            # "Paris centre" → valeur divisée équitablement sur les 4 premiers arr.
            share = col_total / len(arrs)
            for arr in arrs:
                pop_acc[(arr, indicateur)] += share
        else:
            pop_acc[(arrs[0], indicateur)] += col_total

        matched_cols += 1
        logger.debug(
            "Colonne '%s' → arr=%s indicateur=%s total=%.0f",
            col, arrs, indicateur, col_total,
        )

    if not matched_cols:
        logger.warning(
            "Aucune colonne Lden/Ln reconnue dans le dataset. "
            "Colonnes disponibles : %s", list(df_raw.columns)
        )
        return pd.DataFrame(columns=COLS_BRUIT)

    logger.info(
        "%d colonnes traitées → %d combinaisons (arrondissement × indicateur)",
        matched_cols, len(pop_acc),
    )

    # ---- Construire le DataFrame COLS_BRUIT ----
    rows: list[dict] = []
    for (arr, indicateur), pop in sorted(pop_acc.items()):
        rows.append({
            "commune_code":    f"751{str(arr).zfill(2)}",
            "arrondissement":  arr,
            "source_bruit":    "routier",
            "indicateur":      indicateur,
            "tranche_db":      "≥55",
            "surface_ha":      float("nan"),        # non disponible dans ce dataset
            "pop_exposee":     int(round(pop)),
            "pct_pop_exposee": float("nan"),        # non disponible dans ce dataset
            "annee_ref":       None,
            "ingested_at":     ingested_at,
        })

    if not rows:
        logger.warning("Aucune ligne générée après parsing des colonnes Bruitparif")
        return pd.DataFrame(columns=COLS_BRUIT)

    df = pd.DataFrame(rows, columns=COLS_BRUIT)
    logger.info(
        "Bruitparif Paris : %d lignes, %d arrondissements couverts, indicateurs=%s",
        len(df),
        df["arrondissement"].nunique(),
        sorted(df["indicateur"].unique()),
    )
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Recherche et récupération sur data.gouv.fr
# ---------------------------------------------------------------------------

def _search_bruitparif_resources(session: Any, logger: Any) -> list[str]:
    """
    Cherche les ressources CSV du dataset Bruitparif Paris sur data.gouv.fr.

    Requête : GET /api/1/datasets/?q=<DATAGOUV_SEARCH_Q>&page_size=5
    Retourne la liste des URLs CSV trouvées, dans l'ordre de pertinence.
    """
    try:
        resp = session.get(
            f"{DATAGOUV_API}datasets/",
            params={"q": DATAGOUV_SEARCH_Q, "page_size": 5},
            timeout=15,
            verify=False,
        )
        if resp.status_code != 200:
            logger.warning(
                "data.gouv.fr search HTTP %d (q='%s')", resp.status_code, DATAGOUV_SEARCH_Q
            )
            return []

        urls: list[str] = []
        for ds in resp.json().get("data", []):
            title = ds.get("title", "")
            logger.info("Dataset candidat : %s", title)
            for res in ds.get("resources", []):
                fmt = (res.get("format") or "").lower()
                url = res.get("url", "")
                if url and (fmt == "csv" or url.lower().endswith(".csv")):
                    logger.info("  Ressource CSV : %s → %s", res.get("title", ""), url)
                    urls.append(url)

        if not urls:
            logger.warning(
                "Aucune ressource CSV dans les résultats data.gouv.fr pour '%s'",
                DATAGOUV_SEARCH_Q,
            )
        return urls

    except Exception as exc:
        logger.error("Erreur recherche data.gouv.fr Bruitparif : %s", exc)
        return []


def _fetch_bruitparif(session: Any, logger: Any, ingested_at: datetime) -> pd.DataFrame:
    """
    Orchestration :
      1. Recherche du dataset via data.gouv.fr API (mot-clé exact)
      2. Téléchargement + parsing du premier CSV valide trouvé
    """
    urls = _search_bruitparif_resources(session, logger)

    if not urls:
        logger.warning(
            "Bruitparif : aucune ressource CSV disponible. "
            "Vérifier le dataset '%s' sur data.gouv.fr", DATAGOUV_SEARCH_Q
        )
        return pd.DataFrame(columns=COLS_BRUIT)

    for url in urls:
        logger.info("Téléchargement Bruitparif : %s", url)
        try:
            resp = session.get(url, timeout=60, verify=False)
            if resp.status_code != 200:
                logger.warning("HTTP %d sur %s", resp.status_code, url)
                continue

            df_raw = _bytes_to_df(resp.content, logger)
            if df_raw.empty:
                logger.warning("Fichier vide ou non parsable : %s", url)
                continue

            logger.info("Bruitparif brut : %d lignes × %d colonnes", *df_raw.shape)

            df = _parse_bruitparif_paris(df_raw, ingested_at, logger)
            if not df.empty:
                logger.info(
                    "Bruitparif → %d lignes retenues (source : %s)", len(df), url
                )
                return df

        except Exception as exc:
            logger.warning("Erreur sur %s : %s", url, exc)

    logger.warning(
        "Bruitparif : toutes les ressources ont échoué. "
        "Vérifier la disponibilité du dataset '%s' sur data.gouv.fr", DATAGOUV_SEARCH_Q
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
      - Bruitparif (data.gouv.fr) → téléchargement + parsing → Bronze bruitparif
      - SSMSI crime               → lecture Bronze existant (crime.py)

    Sauvegarde :
      data/bronze/bruitparif/date=<date>/part-0.parquet

    Retourne
    --------
    pd.DataFrame
        DataFrame Bruitparif normalisé (source principale de ce module).
    """
    logger = get_logger("tranquility", LOG_DIR)
    ingested_at = datetime.now(timezone.utc)
    run_date = date.today().isoformat()

    logger.info("=" * 60)
    logger.info("Tranquillité vs Dynamisme — ingestion Bronze (%s)", run_date)
    logger.info("=" * 60)

    session = build_session(retries=3, backoff_factor=2.0, timeout=60)

    # --- Source 1 : Bruitparif (data.gouv.fr) ---
    logger.info(">>> Source 1/2 : Bruitparif — Bruit routier Paris (data.gouv.fr)")
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
        print(f"\nShape          : {result.shape}")
        print(f"Arrondissements: {sorted(result['arrondissement'].dropna().unique())}")
        print(f"Indicateurs    : {sorted(result['indicateur'].unique())}")
        print(f"Source bruit   : {sorted(result['source_bruit'].unique())}")
    else:
        print("Aucune donnée Bruitparif disponible.")
        print(f"Conseil : vérifier le dataset '{DATAGOUV_SEARCH_Q}' sur data.gouv.fr")
