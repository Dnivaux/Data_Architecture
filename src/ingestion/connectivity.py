"""
Connectivité & Télétravail — Bronze Ingestion
==============================================
Sources :
  1. ARCEP Mon Réseau Mobile — couverture 4G/5G par commune × opérateur
     Catalogue data.gouv.fr  : https://www.data.gouv.fr/fr/datasets/mon-reseau-mobile/
  2. ARCEP Déploiements Fibre FttH — locaux éligibles par commune
     Catalogue data.gouv.fr  : https://www.data.gouv.fr/fr/datasets/le-marche-du-haut-et-tres-haut-debit-fixe-deploiements/
  3. INSEE RP2020 Logements — répartition par taille (T1 → T5+) par commune
     https://www.insee.fr/fr/statistiques/7705897

Valeur métier
-------------
Croise la connectivité réseau (fibre + mobile) avec la proportion de logements T2/T3
pour identifier les arrondissements les plus adaptés au télétravail pour les jeunes actifs.

Architecture Bronze
-------------------
Les trois sources sont sauvegardées séparément pour préserver la traçabilité :
  data/bronze/arcep_mobile/date=YYYY-MM-DD/part-0.parquet
  data/bronze/arcep_fibre/date=YYYY-MM-DD/part-0.parquet
  data/bronze/insee_logements/date=YYYY-MM-DD/part-0.parquet

La fonction ingest() retourne le DataFrame arcep_mobile comme référence principale.

Schémas Bronze
--------------
arcep_mobile :
  commune_code  str     Code INSEE commune (ex : '75114')
  arrondissement int    1–20 pour Paris
  operateur     str     orange | sfr | bouygues | free
  has_4g        bool    Couverture 4G sur la commune
  has_5g        bool    Couverture 5G sur la commune
  pct_pop_4g    float   % population couverte 4G (si disponible)
  pct_pop_5g    float   % population couverte 5G (si disponible)
  periode       str     Trimestre de référence (ex : '2024-T1')
  ingested_at   datetime

arcep_fibre :
  commune_code        str
  arrondissement      int
  nb_local_ftth       int    Locaux éligibles FttH
  nb_local_total      int    Locaux total
  pct_eligible_ftth   float
  trimestre           str
  ingested_at         datetime

insee_logements :
  commune_code       str
  arrondissement     int
  nb_logements_total int
  nb_t1              int
  nb_t2              int
  nb_t3              int
  nb_t4              int
  nb_t5_plus         int
  pct_t2_t3          float   (nb_t2 + nb_t3) / nb_logements_total
  annee_ref          int
  ingested_at        datetime
"""
from __future__ import annotations

import io
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .base import build_session, get_logger, save_parquet

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LOG_DIR = Path(__file__).parents[2] / "logs"

DATAGOUV_API = "https://www.data.gouv.fr/api/1/datasets/"

# Slugs data.gouv.fr pour la découverte dynamique
ARCEP_MOBILE_SLUG = "mon-reseau-mobile"
ARCEP_FIBRE_SLUG = "le-marche-du-haut-et-tres-haut-debit-fixe-deploiements"

# URLs de fallback (dernières versions connues stables)
ARCEP_MOBILE_FALLBACK = (
    "https://www.data.gouv.fr/fr/datasets/r/"
    "3aeec3c3-f77d-4dc4-81ad-82c6a22dc11a"
)
ARCEP_FIBRE_FALLBACK = (
    "https://www.data.gouv.fr/fr/datasets/r/"
    "8d5a3498-8f44-49e0-9e08-18475f5fe2b0"
)

# INSEE RP2020 logements par commune — ZIP contenant un CSV national
INSEE_LOGCOM_URL = (
    "https://www.insee.fr/fr/statistiques/fichier/7705897/RP2020_logcom_csv.zip"
)

PARIS_CODES = {f"751{str(i).zfill(2)}" for i in range(1, 21)}

# ---------------------------------------------------------------------------
# Colonnes Bronze
# ---------------------------------------------------------------------------

COLS_MOBILE = [
    "commune_code", "arrondissement", "operateur",
    "has_4g", "has_5g", "pct_pop_4g", "pct_pop_5g",
    "periode", "ingested_at",
]
COLS_FIBRE = [
    "commune_code", "arrondissement",
    "nb_local_ftth", "nb_local_total", "pct_eligible_ftth",
    "trimestre", "ingested_at",
]
COLS_LOGEMENTS = [
    "commune_code", "arrondissement",
    "nb_logements_total", "nb_t1", "nb_t2", "nb_t3", "nb_t4", "nb_t5_plus",
    "pct_t2_t3", "annee_ref", "ingested_at",
]

# ---------------------------------------------------------------------------
# Helpers génériques
# ---------------------------------------------------------------------------

def _discover_resource_url(
    session: Any,
    logger: Any,
    slug: str,
    preferred_formats: tuple[str, ...] = ("csv", "zip"),
    fallback_url: str = "",
    keywords: str = "",
) -> str:
    """
    Récupère l'URL de la dernière ressource data.gouv.fr.

    Stratégie :
    1. Accès direct par slug : GET /api/1/datasets/{slug}/  (API v1, syntaxe recommandée)
    2. Recherche par mots-clés : GET /api/1/datasets/?q={keywords}
    3. URL de fallback hardcodée
    """
    # --- Méthode 1 : slug comme chemin (syntaxe correcte API v1) ---
    try:
        resp = session.get(f"{DATAGOUV_API}{slug}/")
        if resp.status_code == 200:
            resources = resp.json().get("resources", [])
            for fmt in preferred_formats:
                candidates = [r for r in resources if r.get("format", "").lower() == fmt]
                if candidates:
                    candidates.sort(
                        key=lambda r: r.get("last_modified", r.get("created_at", "")),
                        reverse=True,
                    )
                    url = candidates[0]["url"]
                    logger.info("Ressource '%s' (%s) : %s", slug, fmt.upper(), url)
                    return url
            if resources:
                url = resources[0]["url"]
                logger.info("Ressource '%s' (format %s) : %s", slug, resources[0].get("format", "?"), url)
                return url
        else:
            logger.warning("data.gouv.fr '%s' → HTTP %d", slug, resp.status_code)
    except Exception as exc:
        logger.warning("Lookup slug '%s' échoué : %s", slug, exc)

    # --- Méthode 2 : recherche par mots-clés ---
    if keywords:
        try:
            resp = session.get(DATAGOUV_API, params={"q": keywords, "page_size": 10, "sort": "-created"})
            if resp.status_code == 200:
                for ds in resp.json().get("data", []):
                    for res in ds.get("resources", []):
                        if res.get("format", "").lower() in preferred_formats:
                            url = res["url"]
                            logger.info("Ressource via recherche '%s' : %s", keywords[:50], url)
                            return url
        except Exception as exc:
            logger.warning("Recherche '%s' échouée : %s", keywords[:50], exc)

    if fallback_url:
        logger.info("Utilisation du fallback : %s", fallback_url)
    return fallback_url


def _download_bytes(session: Any, logger: Any, url: str) -> bytes | None:
    """Télécharge une URL et retourne les bytes bruts."""
    logger.info("Téléchargement : %s", url)
    try:
        resp = session.get(url)
        if resp.status_code != 200:
            logger.error("HTTP %d pour %s", resp.status_code, url)
            return None
        logger.info("  %d Ko reçus", len(resp.content) // 1024)
        return resp.content
    except Exception as exc:
        logger.error("Erreur téléchargement : %s", exc)
        return None


def _bytes_to_df(raw: bytes, logger: Any) -> pd.DataFrame:
    """
    Convertit bytes en DataFrame.
    Gère : ZIP contenant un CSV, CSV direct, Parquet.
    """
    # Tentative ZIP
    if raw[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                csvs = [n for n in zf.namelist() if n.lower().endswith(".csv")]
                if csvs:
                    with zf.open(csvs[0]) as f:
                        logger.debug("ZIP → CSV '%s'", csvs[0])
                        for enc in ("utf-8", "latin-1", "utf-8-sig"):
                            try:
                                return pd.read_csv(f, sep=None, engine="python", encoding=enc, low_memory=False)
                            except UnicodeDecodeError:
                                f.seek(0)
                parquets = [n for n in zf.namelist() if n.lower().endswith(".parquet")]
                if parquets:
                    with zf.open(parquets[0]) as f:
                        return pd.read_parquet(io.BytesIO(f.read()), engine="pyarrow")
        except zipfile.BadZipFile:
            pass

    # Tentative Parquet
    try:
        return pd.read_parquet(io.BytesIO(raw), engine="pyarrow")
    except Exception:
        pass

    # Tentative CSV
    for sep in (";", ",", "\t"):
        for enc in ("utf-8", "latin-1", "utf-8-sig"):
            try:
                return pd.read_csv(io.BytesIO(raw), sep=sep, encoding=enc, low_memory=False)
            except Exception:
                continue

    logger.error("Impossible de parser le fichier téléchargé")
    return pd.DataFrame()


def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """Retourne le premier nom de colonne présent dans df parmi les candidats."""
    cols_lower = {c.lower(): c for c in df.columns}
    for c in candidates:
        if c in df.columns:
            return c
        if c.lower() in cols_lower:
            return cols_lower[c.lower()]
    return None


# ---------------------------------------------------------------------------
# Source 1 — ARCEP Mon Réseau Mobile (sites géolocalisés → jointure spatiale)
# ---------------------------------------------------------------------------
# Le fichier ARCEP sites mobiles liste les antennes avec latitude/longitude.
# Paris est codée comme "75056" (pas par arrondissement). On fait une jointure
# spatiale pour assigner chaque antenne à un arrondissement, puis on compte
# les sites 4G/5G par opérateur et arrondissement comme indicateur de densité réseau.

_ARCEP_SITES_BASE = "https://data.arcep.fr/mobile/sites/"

_OP_NORMALIZE = {
    "orange": "orange",
    "sfr": "sfr",
    "bouygues telecom": "bouygues",
    "free mobile": "free",
    "free": "free",
    "bouygues": "bouygues",
}


def _fetch_arcep_mobile(
    session: Any, logger: Any, ingested_at: datetime, run_date: str
) -> pd.DataFrame:
    import geopandas as gpd

    # Découvrir le dernier trimestre disponible
    try:
        resp = session.get(_ARCEP_SITES_BASE)
        trimestres = sorted(
            [t.split("/")[0] for t in __import__("re").findall(r'href="(\d{4}_T\d/index\.html)"', resp.text)],
            reverse=True
        )
        dernierT = trimestres[0] if trimestres else "2025_T4"
    except Exception:
        dernierT = "2025_T4"

    url_csv = f"{_ARCEP_SITES_BASE}{dernierT}/{ dernierT}_sites_Metropole.csv"
    logger.info("ARCEP sites mobiles : %s (trimestre %s)", url_csv, dernierT)

    raw = _download_bytes(session, logger, url_csv)
    if raw is None:
        return pd.DataFrame(columns=COLS_MOBILE)

    try:
        df_sites = pd.read_csv(io.BytesIO(raw), sep=";", low_memory=False)
    except Exception as exc:
        logger.error("Erreur lecture CSV sites mobiles : %s", exc)
        return pd.DataFrame(columns=COLS_MOBILE)

    logger.info("Sites mobiles bruts : %d lignes", len(df_sites))

    # Filtrer Paris (code INSEE = 75056)
    df_paris = df_sites[df_sites["insee_com"].astype(str) == "75056"].copy()
    if df_paris.empty:
        logger.warning("Aucun site Paris (75056) — CSV vide ou format inattendu")
        return pd.DataFrame(columns=COLS_MOBILE)

    logger.info("Sites Paris : %d antennes", len(df_paris))

    # Nettoyer latitude/longitude (format français : virgule décimale)
    for col_ll in ("latitude", "longitude"):
        df_paris[col_ll] = (
            df_paris[col_ll].astype(str).str.replace(",", ".").pipe(pd.to_numeric, errors="coerce")
        )
    df_paris = df_paris.dropna(subset=["latitude", "longitude"])

    # Jointure spatiale avec les boundaries des arrondissements
    try:
        from src.ingestion.base import read_parquet
        boundaries = read_parquet("boundaries")
        geom_col = "geometry_wkt" if "geometry_wkt" in boundaries.columns else "geometry"
        bounds_gdf = gpd.GeoDataFrame(
            boundaries[["arrondissement"]],
            geometry=gpd.GeoSeries.from_wkt(boundaries[geom_col]),
            crs="EPSG:4326",
        )
        sites_gdf = gpd.GeoDataFrame(
            df_paris,
            geometry=gpd.points_from_xy(df_paris["longitude"], df_paris["latitude"]),
            crs="EPSG:4326",
        )
        joined = gpd.sjoin(sites_gdf, bounds_gdf, how="left", predicate="within")
        df_paris = pd.DataFrame(joined)
    except Exception as exc:
        logger.warning("Jointure spatiale échouée (%s) — arrondissement non disponible", exc)
        df_paris["arrondissement"] = None

    if "arrondissement" not in df_paris.columns or df_paris["arrondissement"].isna().all():
        logger.warning("Arrondissement non assigné — données mobile insuffisantes")
        return pd.DataFrame(columns=COLS_MOBILE)

    df_paris = df_paris.dropna(subset=["arrondissement"])
    df_paris["arrondissement"] = df_paris["arrondissement"].astype(int)

    # Normaliser le nom de l'opérateur
    df_paris["operateur"] = df_paris["nom_op"].astype(str).str.lower().map(
        lambda x: _OP_NORMALIZE.get(x, x.split()[0] if x else "inconnu")
    )

    # Compter les sites 4G et 5G par opérateur × arrondissement
    df_paris["site_4g"] = pd.to_numeric(df_paris["site_4g"], errors="coerce").fillna(0).astype(int)
    df_paris["site_5g"] = pd.to_numeric(df_paris["site_5g"], errors="coerce").fillna(0).astype(int)

    counts = (
        df_paris.groupby(["arrondissement", "operateur"])
        .agg(sites_4g=("site_4g", "sum"), sites_5g=("site_5g", "sum"))
        .reset_index()
    )

    # Calculer les % relatifs par rapport au total par arrondissement
    total_4g = counts.groupby("arrondissement")["sites_4g"].sum().rename("total_4g")
    total_5g = counts.groupby("arrondissement")["sites_5g"].sum().rename("total_5g")
    counts = counts.join(total_4g, on="arrondissement").join(total_5g, on="arrondissement")

    counts["pct_pop_4g"] = (counts["sites_4g"] / counts["total_4g"] * 100).round(1).fillna(0)
    counts["pct_pop_5g"] = (counts["sites_5g"] / counts["total_5g"] * 100).round(1).fillna(0)
    counts["has_4g"]     = counts["sites_4g"] > 0
    counts["has_5g"]     = counts["sites_5g"] > 0
    counts["commune_code"] = "75056"
    counts["periode"]    = dernierT
    counts["ingested_at"] = ingested_at

    return counts[COLS_MOBILE].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Source 2 — ARCEP Déploiements Fibre (Shapefile communes 2025T4)
# ---------------------------------------------------------------------------
# Source : shapefile ZIP "2025T4-Commune" sur data.gouv.fr
# Colonnes utiles : INSEE_COM, Locaux (total), ftth (locaux éligibles FTTH)
# pct_eligible_ftth = ftth / Locaux × 100  (valeurs réelles par arrondissement)

def _fetch_fibre_commune_url(session: Any, logger: Any) -> str:
    """Trouve l'URL du shapefile ZIP 2025T4-Commune sur data.gouv.fr."""
    try:
        resp = session.get(f"{DATAGOUV_API}{ARCEP_FIBRE_SLUG}/")
        if resp.status_code == 200:
            resources = resp.json().get("resources", [])
            # Chercher le plus récent fichier Commune en ZIP
            communes = sorted(
                [r for r in resources if "commune" in r.get("title", "").lower() and r.get("format", "") == "zip"],
                key=lambda r: r.get("last_modified", r.get("created_at", "")),
                reverse=True,
            )
            if communes:
                return communes[0]["url"]
    except Exception as exc:
        logger.warning("Découverte URL fibre échouée : %s", exc)
    return ARCEP_FIBRE_FALLBACK


def _fetch_arcep_fibre(
    session: Any, logger: Any, ingested_at: datetime, run_date: str
) -> pd.DataFrame:
    import geopandas as gpd
    import tempfile, os

    url = _fetch_fibre_commune_url(session, logger)
    logger.info("ARCEP fibre Commune ZIP : %s", url)

    raw = _download_bytes(session, logger, url)
    if raw is None:
        return pd.DataFrame(columns=COLS_FIBRE)

    # Extraire le shapefile et le lire avec geopandas
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                zf.extractall(tmpdir)
            shp_files = [f for f in os.listdir(tmpdir) if f.endswith(".shp")]
            if not shp_files:
                logger.error("Aucun .shp dans le ZIP fibre")
                return pd.DataFrame(columns=COLS_FIBRE)
            gdf = gpd.read_file(os.path.join(tmpdir, shp_files[0]))
    except Exception as exc:
        logger.error("Erreur lecture shapefile fibre : %s", exc)
        return pd.DataFrame(columns=COLS_FIBRE)

    logger.info("Fibre shapefile : %d communes, colonnes=%s", len(gdf), gdf.columns.tolist()[:10])

    # Filtrer Paris (codes 75101–75120)
    paris = gdf[gdf["INSEE_COM"].astype(str).str.startswith("751")].copy()
    if paris.empty:
        logger.warning("Aucun arrondissement parisien trouvé dans le shapefile fibre")
        return pd.DataFrame(columns=COLS_FIBRE)

    logger.info("Fibre Paris : %d arrondissements", len(paris))

    paris["arrondissement"]    = paris["INSEE_COM"].astype(str).str[-2:].astype(int)
    paris["nb_local_total"]    = pd.to_numeric(paris["Locaux"], errors="coerce")
    paris["nb_local_ftth"]     = pd.to_numeric(paris["ftth"],   errors="coerce")
    paris["pct_eligible_ftth"] = (paris["nb_local_ftth"] / paris["nb_local_total"] * 100).round(2)
    paris["commune_code"]      = paris["INSEE_COM"].astype(str)
    paris["trimestre"]         = run_date
    paris["ingested_at"]       = ingested_at

    return paris[COLS_FIBRE].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Source 3 — INSEE RP2020 Logements par taille
# ---------------------------------------------------------------------------

# Noms de colonnes stables de l'INSEE RP2020 (logements par nombre de pièces)
_INSEE_COL_MAP = {
    "CODGEO":        "commune_code",
    "P20_RP":        "nb_logements_total",   # Résidences principales
    "P20_RP_1P":     "nb_t1",                # 1 pièce = T1
    "P20_RP_2P":     "nb_t2",                # 2 pièces = T2
    "P20_RP_3P":     "nb_t3",                # 3 pièces = T3
    "P20_RP_4P":     "nb_t4",                # 4 pièces = T4
    "P20_RP_5PP":    "nb_t5_plus",           # 5+ pièces = T5+
}


def _fetch_insee_logements(
    session: Any, logger: Any, ingested_at: datetime
) -> pd.DataFrame:
    raw = _download_bytes(session, logger, INSEE_LOGCOM_URL)
    if raw is None:
        return pd.DataFrame(columns=COLS_LOGEMENTS)

    df_raw = _bytes_to_df(raw, logger)
    if df_raw.empty:
        return pd.DataFrame(columns=COLS_LOGEMENTS)

    logger.info("INSEE logements brut : %d × %d", *df_raw.shape)

    # Vérifier la présence des colonnes attendues
    missing = [c for c in _INSEE_COL_MAP if c not in df_raw.columns]
    if missing:
        logger.warning("Colonnes INSEE manquantes : %s", missing)
        logger.debug("Colonnes disponibles : %s", list(df_raw.columns)[:20])

    # Filtre Paris (codes 75101–75120)
    code_col = _find_col(df_raw, ["CODGEO", "codgeo", "code_commune", "commune_code"])
    if not code_col:
        logger.error("Colonne CODGEO introuvable dans le fichier INSEE")
        return pd.DataFrame(columns=COLS_LOGEMENTS)

    df_paris = df_raw[df_raw[code_col].astype(str).str.strip().isin(PARIS_CODES)].copy()
    logger.info("INSEE logements Paris : %d arrondissements", len(df_paris))

    def _safe_int(col_name: str) -> pd.Series:
        if col_name in df_paris.columns:
            return pd.to_numeric(df_paris[col_name], errors="coerce").fillna(0).astype("Int64")
        return pd.Series([pd.NA] * len(df_paris), dtype="Int64")

    nb_total = _safe_int("P20_RP")
    nb_t2    = _safe_int("P20_RP_2P")
    nb_t3    = _safe_int("P20_RP_3P")

    pct_t2_t3 = ((nb_t2 + nb_t3) / nb_total * 100).round(2).where(nb_total > 0)

    rows = pd.DataFrame({
        "commune_code":       df_paris[code_col].astype(str).str.strip(),
        "arrondissement":     df_paris[code_col].astype(str).str.strip().str[-2:].astype(int),
        "nb_logements_total": nb_total,
        "nb_t1":              _safe_int("P20_RP_1P"),
        "nb_t2":              nb_t2,
        "nb_t3":              nb_t3,
        "nb_t4":              _safe_int("P20_RP_4P"),
        "nb_t5_plus":         _safe_int("P20_RP_5PP"),
        "pct_t2_t3":          pct_t2_t3,
        "annee_ref":          2020,
        "ingested_at":        ingested_at,
    })

    return rows[COLS_LOGEMENTS].sort_values("arrondissement").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Point d'entrée public
# ---------------------------------------------------------------------------

def ingest() -> pd.DataFrame:
    """
    Ingère les trois sources de l'indicateur Connectivité & Télétravail.

    Sauvegarde :
      data/bronze/arcep_mobile/date=<date>/part-0.parquet
      data/bronze/arcep_fibre/date=<date>/part-0.parquet
      data/bronze/insee_logements/date=<date>/part-0.parquet

    Retourne
    --------
    pd.DataFrame
        DataFrame arcep_mobile (source principale, aussi écrit en Parquet).
    """
    logger = get_logger("connectivity", LOG_DIR)
    ingested_at = datetime.now(timezone.utc)
    run_date = date.today().isoformat()

    logger.info("=" * 60)
    logger.info("Connectivité & Télétravail — ingestion Bronze (%s)", run_date)
    logger.info("=" * 60)

    session = build_session(retries=3, backoff_factor=1.5, timeout=120)

    # --- Source 1 : ARCEP Mobile ---
    logger.info(">>> Source 1/3 : ARCEP Mon Réseau Mobile (4G/5G)")
    df_mobile = _fetch_arcep_mobile(session, logger, ingested_at, run_date)
    if not df_mobile.empty:
        path = save_parquet(df_mobile, "arcep_mobile",
                            partition_col="date", partition_value=run_date,
                            filename="part-0.parquet")
        logger.info("ARCEP mobile → %d lignes sauvegardées : %s", len(df_mobile), path)
    else:
        logger.warning("ARCEP mobile : aucune donnée Paris — fichier Bronze non créé")

    # --- Source 2 : ARCEP Fibre ---
    logger.info(">>> Source 2/3 : ARCEP Déploiements Fibre FttH")
    df_fibre = _fetch_arcep_fibre(session, logger, ingested_at, run_date)
    if not df_fibre.empty:
        path = save_parquet(df_fibre, "arcep_fibre",
                            partition_col="date", partition_value=run_date,
                            filename="part-0.parquet")
        logger.info("ARCEP fibre → %d lignes sauvegardées : %s", len(df_fibre), path)
    else:
        logger.warning("ARCEP fibre : aucune donnée Paris — fichier Bronze non créé")

    # --- Source 3 : INSEE Logements ---
    logger.info(">>> Source 3/3 : INSEE RP2020 Logements par taille")
    df_logements = _fetch_insee_logements(session, logger, ingested_at)
    if not df_logements.empty:
        path = save_parquet(df_logements, "insee_logements",
                            partition_col="date", partition_value=run_date,
                            filename="part-0.parquet")
        logger.info("INSEE logements → %d arrondissements sauvegardés : %s", len(df_logements), path)
    else:
        logger.warning("INSEE logements : aucune donnée Paris — fichier Bronze non créé")

    logger.info("Connectivité & Télétravail — ingestion terminée")
    return df_mobile


if __name__ == "__main__":
    result = ingest()
    if not result.empty:
        print("\n--- Aperçu ARCEP Mobile (Bronze) ---")
        print(result.head(10).to_string(index=False))
        print(f"\nShape : {result.shape}")
        print(f"Opérateurs : {sorted(result['operateur'].unique())}")
