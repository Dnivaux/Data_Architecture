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
) -> str:
    """Interroge l'API data.gouv.fr pour trouver la dernière ressource d'un dataset."""
    try:
        resp = session.get(f"{DATAGOUV_API}?slug={slug}&page_size=1")
        if resp.status_code != 200:
            logger.warning("data.gouv.fr API HTTP %d pour '%s'", resp.status_code, slug)
            return fallback_url

        results = resp.json().get("data", [])
        if not results:
            logger.warning("Aucun dataset trouvé pour slug '%s'", slug)
            return fallback_url

        resources = results[0].get("resources", [])
        # Priorité : ressource la plus récente au format préféré
        for fmt in preferred_formats:
            candidates = [
                r for r in resources
                if r.get("format", "").lower() == fmt
            ]
            if candidates:
                # Trier par date de création décroissante
                candidates.sort(key=lambda r: r.get("created_at", ""), reverse=True)
                url = candidates[0]["url"]
                logger.info("Ressource découverte (%s) : %s", fmt.upper(), url)
                return url

        logger.warning("Aucune ressource %s trouvée — fallback", preferred_formats)
    except Exception as exc:
        logger.warning("Erreur découverte '%s' : %s — fallback", slug, exc)

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
# Source 1 — ARCEP Mon Réseau Mobile
# ---------------------------------------------------------------------------

# Variantes de noms de colonnes observées selon le millésime du fichier ARCEP
_MOBILE_CODE_VARIANTS = ["code_commune", "code_insee", "CODGEO", "Code_commune_INSEE",
                          "code_commune_insee", "x_com", "COM"]
_MOBILE_OP_VARIANTS   = ["operateur", "OPERATEUR", "Operateur", "operator"]
_MOBILE_4G_VARIANTS   = ["4G", "couverture_4G", "4g", "COUVR_4G", "Zone_4G", "ZONE_4G",
                          "4g_couverture", "couv_4g"]
_MOBILE_5G_VARIANTS   = ["5G", "couverture_5G", "5g", "COUVR_5G", "Zone_5G", "ZONE_5G",
                          "5g_couverture", "couv_5g"]
_MOBILE_PCT4G_VARIANTS = ["pct_pop_4g", "pourcentage_4g", "taux_4g", "pop_couv_4g"]
_MOBILE_PCT5G_VARIANTS = ["pct_pop_5g", "pourcentage_5g", "taux_5g", "pop_couv_5g"]
_MOBILE_PERIODE_VARIANTS = ["periode", "trimestre", "TRIMESTRE", "annee", "année", "date"]


def _fetch_arcep_mobile(
    session: Any, logger: Any, ingested_at: datetime, run_date: str
) -> pd.DataFrame:
    url = _discover_resource_url(
        session, logger, ARCEP_MOBILE_SLUG,
        preferred_formats=("csv", "zip"),
        fallback_url=ARCEP_MOBILE_FALLBACK,
    )
    raw = _download_bytes(session, logger, url)
    if raw is None:
        return pd.DataFrame(columns=COLS_MOBILE)

    df_raw = _bytes_to_df(raw, logger)
    if df_raw.empty:
        return pd.DataFrame(columns=COLS_MOBILE)

    logger.info("ARCEP mobile brut : %d × %d", *df_raw.shape)

    # Détection flexible des colonnes
    col_code   = _find_col(df_raw, _MOBILE_CODE_VARIANTS)
    col_op     = _find_col(df_raw, _MOBILE_OP_VARIANTS)
    col_4g     = _find_col(df_raw, _MOBILE_4G_VARIANTS)
    col_5g     = _find_col(df_raw, _MOBILE_5G_VARIANTS)
    col_pct4g  = _find_col(df_raw, _MOBILE_PCT4G_VARIANTS)
    col_pct5g  = _find_col(df_raw, _MOBILE_PCT5G_VARIANTS)
    col_period = _find_col(df_raw, _MOBILE_PERIODE_VARIANTS)

    if not col_code:
        logger.error("Colonne code commune introuvable dans ARCEP mobile. Colonnes : %s", list(df_raw.columns)[:15])
        return pd.DataFrame(columns=COLS_MOBILE)

    # Filtre Paris
    df_paris = df_raw[df_raw[col_code].astype(str).str.strip().isin(PARIS_CODES)].copy()
    logger.info("ARCEP mobile Paris : %d lignes", len(df_paris))

    if df_paris.empty:
        logger.warning("Aucune commune parisienne trouvée — vérifier le format du fichier ARCEP mobile")
        return pd.DataFrame(columns=COLS_MOBILE)

    def _to_bool(series: pd.Series) -> pd.Series:
        """Convertit 0/1, 'oui'/'non', True/False en bool."""
        s = series.astype(str).str.strip().str.lower()
        return s.isin(["1", "oui", "true", "vrai", "o", "yes"])

    rows = pd.DataFrame({
        "commune_code":  df_paris[col_code].astype(str).str.strip(),
        "arrondissement": df_paris[col_code].astype(str).str.strip().str[-2:].astype(int),
        "operateur":     df_paris[col_op].astype(str).str.lower() if col_op else "inconnu",
        "has_4g":        _to_bool(df_paris[col_4g]) if col_4g else False,
        "has_5g":        _to_bool(df_paris[col_5g]) if col_5g else False,
        "pct_pop_4g":    pd.to_numeric(df_paris[col_pct4g], errors="coerce") if col_pct4g else float("nan"),
        "pct_pop_5g":    pd.to_numeric(df_paris[col_pct5g], errors="coerce") if col_pct5g else float("nan"),
        "periode":       df_paris[col_period].astype(str) if col_period else run_date,
        "ingested_at":   ingested_at,
    })

    return rows[COLS_MOBILE].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Source 2 — ARCEP Déploiements Fibre
# ---------------------------------------------------------------------------

_FIBRE_CODE_VARIANTS    = ["code_commune", "code_insee", "CODGEO", "code_commune_insee",
                            "CodeINSEE", "code_dep_com"]
_FIBRE_FTTH_VARIANTS    = ["nb_local_ftth", "locaux_ftth", "nb_locaux_ftth", "locaux_eligibles_ftth",
                            "ftth_locaux", "locaux_raccordables"]
_FIBRE_TOTAL_VARIANTS   = ["nb_local_total", "locaux_total", "nb_locaux_total", "total_locaux",
                            "nb_locaux"]
_FIBRE_PERIODE_VARIANTS = ["trimestre", "TRIMESTRE", "periode", "date", "annee"]


def _fetch_arcep_fibre(
    session: Any, logger: Any, ingested_at: datetime, run_date: str
) -> pd.DataFrame:
    url = _discover_resource_url(
        session, logger, ARCEP_FIBRE_SLUG,
        preferred_formats=("csv", "zip"),
        fallback_url=ARCEP_FIBRE_FALLBACK,
    )
    raw = _download_bytes(session, logger, url)
    if raw is None:
        return pd.DataFrame(columns=COLS_FIBRE)

    df_raw = _bytes_to_df(raw, logger)
    if df_raw.empty:
        return pd.DataFrame(columns=COLS_FIBRE)

    logger.info("ARCEP fibre brut : %d × %d", *df_raw.shape)

    col_code   = _find_col(df_raw, _FIBRE_CODE_VARIANTS)
    col_ftth   = _find_col(df_raw, _FIBRE_FTTH_VARIANTS)
    col_total  = _find_col(df_raw, _FIBRE_TOTAL_VARIANTS)
    col_period = _find_col(df_raw, _FIBRE_PERIODE_VARIANTS)

    if not col_code:
        logger.error("Colonne code commune introuvable dans ARCEP fibre. Colonnes : %s", list(df_raw.columns)[:15])
        return pd.DataFrame(columns=COLS_FIBRE)

    df_paris = df_raw[df_raw[col_code].astype(str).str.strip().isin(PARIS_CODES)].copy()
    logger.info("ARCEP fibre Paris : %d lignes", len(df_paris))

    nb_ftth  = pd.to_numeric(df_paris[col_ftth],  errors="coerce") if col_ftth  else pd.Series([float("nan")] * len(df_paris))
    nb_total = pd.to_numeric(df_paris[col_total], errors="coerce") if col_total else pd.Series([float("nan")] * len(df_paris))

    pct_ftth = (nb_ftth / nb_total * 100).round(2).where(nb_total > 0)

    rows = pd.DataFrame({
        "commune_code":      df_paris[col_code].astype(str).str.strip(),
        "arrondissement":    df_paris[col_code].astype(str).str.strip().str[-2:].astype(int),
        "nb_local_ftth":     nb_ftth.astype("Int64"),
        "nb_local_total":    nb_total.astype("Int64"),
        "pct_eligible_ftth": pct_ftth,
        "trimestre":         df_paris[col_period].astype(str) if col_period else run_date,
        "ingested_at":       ingested_at,
    })

    return rows[COLS_FIBRE].reset_index(drop=True)


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
