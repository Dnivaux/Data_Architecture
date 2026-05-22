"""
ICAR Référentiel — Bronze Ingestion (NeTEx streaming)
======================================================
Source : IDFM Marketplace — Infrastructure Commune des Arrêts et des lieux (ICAR)
API    : GET https://prim.iledefrance-mobilites.fr/marketplace/icar/getData
Format : NeTEx XML (~141 MB, EPSG:2154 Lambert 93)

Stratégie :
  - Streaming HTTP avec `requests` (pas de chargement en mémoire)
  - Parsing incrémental `lxml.etree.iterparse` sur tag <StopPlace>
  - Reprojection EPSG:2154 → WGS84 (EPSG:4326) via `pyproj`
  - Filtre géographique Paris (lat 48.80–48.92 / lon 2.22–2.48)

Produit :
  data/bronze/icar/part-0.parquet
  Colonnes : stop_id, stop_name, latitude, longitude, transport_mode, batch_ts

Utilisation
-----------
  # Ingestion complète
  python -m src.ingestion.icar_referentiel

  # Aperçu 200 arrêts sans sauvegarde
  python -m src.ingestion.icar_referentiel --dry-run --limit 200

  # Ingestion limitée pour test
  python -m src.ingestion.icar_referentiel --limit 1000
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from lxml import etree
from pyproj import Transformer

from .base import build_session, get_logger, save_parquet

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

LOG_DIR = Path(__file__).parents[2] / "logs"

ICAR_URL     = "https://prim.iledefrance-mobilites.fr/marketplace/icar/getData"
ICAR_API_KEY = "ilSL1XSO6AFLBcZfz1ere53AjjdG4cco"

ICAR_HEADERS = {
    "apikey": ICAR_API_KEY,
    # Pas de Accept: application/json — l'API retourne du XML (NeTEx)
    "Accept": "application/xml",
}

# NeTEx namespaces
_NETEX_NS = "http://www.netex.org.uk/netex"
_GML_NS   = "http://www.opengis.net/gml/3.2"

# Délai de backoff initial sur 429
_RATE_LIMIT_BASE_WAIT    = 10   # secondes
_RATE_LIMIT_MAX_RETRIES  = 5

# Bounding box Paris stricte (WGS84) pour filtrer les arrêts hors région
_PARIS_LAT_MIN, _PARIS_LAT_MAX = 48.80, 48.92
_PARIS_LON_MIN, _PARIS_LON_MAX = 2.22,  2.48

# Colonnes garanties dans le Parquet de sortie
ICAR_BRONZE_COLUMNS = [
    "stop_id",
    "stop_name",
    "latitude",
    "longitude",
    "transport_mode",
    "batch_ts",
]

# Mapping NeTEx StopPlaceType → label normalisé transport_mode
_TYPE_TO_MODE: dict[str, str] = {
    "metroStation":     "metro",
    "railStation":      "rer",
    "onstreetTram":     "tram",
    "tramStation":      "tram",
    "onstreetBus":      "bus",
    "busStation":       "bus",
    "coachStation":     "coach",
    "ferryStop":        "ferry",
    "liftStation":      "funicular",
    "multimodal":       "multimodal",
    "airport":          "airport",
    "harbourPort":      "ferry",
}


# ---------------------------------------------------------------------------
# Reprojection EPSG:2154 → EPSG:4326
# ---------------------------------------------------------------------------

# Singleton — initialisé une seule fois (thread-safe en lecture)
_TRANSFORMER: Transformer | None = None


def _get_transformer() -> Transformer:
    global _TRANSFORMER
    if _TRANSFORMER is None:
        _TRANSFORMER = Transformer.from_crs("EPSG:2154", "EPSG:4326", always_xy=True)
    return _TRANSFORMER


def _lambert93_to_wgs84(x: float, y: float) -> tuple[float, float]:
    """Convertit les coordonnées Lambert 93 (x=E, y=N) en (lat, lon) WGS84."""
    lon, lat = _get_transformer().transform(x, y)
    return lat, lon


# ---------------------------------------------------------------------------
# Parser NeTEx streaming
# ---------------------------------------------------------------------------

def _tag(localname: str) -> str:
    """Retourne le tag qualifié avec le namespace NeTEx."""
    return f"{{{_NETEX_NS}}}{localname}"


def _gml_tag(localname: str) -> str:
    return f"{{{_GML_NS}}}{localname}"


def _parse_netex_streaming(raw_stream, logger) -> list[dict]:
    """
    Parse le flux NeTEx XML en streaming avec iterparse.
    Extrait uniquement les éléments <StopPlace> (arrêts physiques).

    Gestion mémoire :
      - elem.clear() après traitement de chaque StopPlace → O(1) RAM
      - Idéal pour les fichiers > 100 MB

    Retourne la liste de dicts normalisés (avant filtre géographique).
    """
    rows:      list[dict] = []
    skipped:   int = 0
    no_coords: int = 0
    count:     int = 0

    # iterparse sur le flux HTTP brut (déclenche sur la fermeture de <StopPlace>)
    context = etree.iterparse(
        raw_stream,
        events=("end",),
        tag=_tag("StopPlace"),
        recover=True,      # tolère les malformations mineures
        huge_tree=True,    # autorise les arbres profonds (NeTEx peut être verbeux)
    )

    for _event, elem in context:
        count += 1

        # --- stop_id ---
        stop_id = elem.get("id", "").strip()
        if not stop_id:
            skipped += 1
            elem.clear()
            continue

        # --- stop_name ---
        name_el = elem.find(_tag("Name"))
        stop_name = (name_el.text or "").strip() if name_el is not None else stop_id

        # --- Coordonnées : gml:pos en EPSG:2154 ---
        pos_el = elem.find(f".//{_gml_tag('pos')}")
        if pos_el is None or not (pos_el.text or "").strip():
            no_coords += 1
            elem.clear()
            continue

        parts = pos_el.text.strip().split()
        if len(parts) < 2:
            no_coords += 1
            elem.clear()
            continue

        try:
            x, y = float(parts[0]), float(parts[1])
            lat, lon = _lambert93_to_wgs84(x, y)
        except (ValueError, TypeError) as exc:
            logger.debug("Reprojection échouée pour %s : %s", stop_id, exc)
            no_coords += 1
            elem.clear()
            continue

        # --- transport_mode depuis StopPlaceType ---
        type_el = elem.find(_tag("StopPlaceType"))
        stop_type = (type_el.text or "").strip() if type_el is not None else ""
        mode = _TYPE_TO_MODE.get(stop_type, "unknown")

        rows.append({
            "stop_id":        stop_id,
            "stop_name":      stop_name,
            "latitude":       lat,
            "longitude":      lon,
            "transport_mode": mode,
        })

        # Libère la mémoire de l'élément traité
        elem.clear()
        # Supprime également les ancêtres résolus pour éviter les fuites
        while elem.getprevious() is not None:
            del elem.getparent()[0]

    logger.info(
        "iterparse terminé — %d StopPlace traités : %d avec coords, "
        "%d sans coords, %d sans id",
        count, len(rows), no_coords, skipped,
    )
    return rows


# ---------------------------------------------------------------------------
# Appel HTTP avec gestion 429 / backoff exponentiel
# ---------------------------------------------------------------------------

def _stream_icar(session, logger):
    """
    Ouvre une connexion HTTP streaming vers l'API ICAR.
    Gère les codes 429 avec backoff exponentiel.
    Retourne l'objet Response (streaming, non consommé).
    """
    wait = _RATE_LIMIT_BASE_WAIT

    for attempt in range(1, _RATE_LIMIT_MAX_RETRIES + 1):
        logger.info(
            "ICAR → tentative %d/%d : GET %s",
            attempt, _RATE_LIMIT_MAX_RETRIES, ICAR_URL,
        )
        try:
            resp = session.get(
                ICAR_URL,
                headers=ICAR_HEADERS,
                stream=True,
                timeout=120,
            )
        except Exception as exc:
            logger.error("ICAR connexion échouée (tentative %d) : %s", attempt, exc)
            if attempt == _RATE_LIMIT_MAX_RETRIES:
                raise
            time.sleep(wait)
            wait = min(wait * 2, 120)
            continue

        logger.debug(
            "ICAR HTTP %d — Content-Type : %s",
            resp.status_code,
            resp.headers.get("Content-Type", "?"),
        )

        if resp.status_code == 200:
            ct = resp.headers.get("Content-Type", "")
            if "xml" not in ct.lower():
                logger.warning("Content-Type inattendu : %s (attendu XML)", ct)
            return resp

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", wait))
            remaining   = resp.headers.get("x-ratelimit-remaining-day", "?")
            logger.warning(
                "ICAR rate-limit 429 — Retry-After: %ds | quota restant: %s "
                "(tentative %d/%d)",
                retry_after, remaining, attempt, _RATE_LIMIT_MAX_RETRIES,
            )
            if attempt == _RATE_LIMIT_MAX_RETRIES:
                raise RuntimeError(
                    f"ICAR rate-limit persistant après {_RATE_LIMIT_MAX_RETRIES} tentatives"
                )
            time.sleep(retry_after)
            wait = min(retry_after * 2, 120)
            continue

        if resp.status_code == 401:
            raise RuntimeError(
                f"ICAR clé API invalide ou manquante (HTTP 401) : {resp.text[:200]}"
            )
        if resp.status_code == 403:
            raise RuntimeError(
                f"ICAR accès refusé (HTTP 403) : {resp.text[:200]}"
            )
        if resp.status_code == 404:
            raise RuntimeError(
                f"ICAR endpoint introuvable (HTTP 404) : {ICAR_URL}"
            )

        logger.warning(
            "ICAR HTTP %d — corps : %s (tentative %d/%d)",
            resp.status_code, resp.text[:200], attempt, _RATE_LIMIT_MAX_RETRIES,
        )
        if attempt == _RATE_LIMIT_MAX_RETRIES:
            resp.raise_for_status()
        time.sleep(wait)
        wait = min(wait * 2, 120)

    raise RuntimeError("ICAR : nombre maximum de tentatives atteint")


# ---------------------------------------------------------------------------
# Interface publique
# ---------------------------------------------------------------------------

def ingest_icar(limit: int | None = None, dry_run: bool = False) -> pd.DataFrame:
    """
    Télécharge le référentiel ICAR et persiste le résultat en Bronze Parquet.

    Le fichier NeTEx (~141 MB) est parsé en streaming : la mémoire utilisée
    reste proportionnelle au nombre d'arrêts extraits, pas à la taille du XML.

    Paramètres
    ----------
    limit    : int | None
        Si défini, tronque à N arrêts parisiens après filtrage géographique.
    dry_run  : bool
        Si True, n'écrit pas de fichier Parquet.

    Retourne
    --------
    DataFrame Bronze avec ICAR_BRONZE_COLUMNS.
    """
    logger  = get_logger("icar_referentiel", LOG_DIR)
    session = build_session(retries=3, backoff_factor=2.0, timeout=120)

    batch_ts = datetime.now(timezone.utc)
    logger.info("=" * 60)
    logger.info("ICAR Référentiel — ingestion Bronze démarrée")
    logger.info("Timestamp batch : %s", batch_ts.isoformat())
    logger.info("Format         : NeTEx XML (EPSG:2154 → WGS84)")
    logger.info("=" * 60)

    # 1. Ouverture du flux HTTP
    try:
        resp = _stream_icar(session, logger)
    except Exception as exc:
        logger.error("ICAR ingestion annulée : %s", exc)
        return pd.DataFrame(columns=ICAR_BRONZE_COLUMNS)

    # 2. Parsing NeTEx streaming
    logger.info("Parsing NeTEx streaming en cours (fichier ~141 MB)...")
    # urllib3 doit décompresser le flux gzip avant de le passer à lxml
    resp.raw.decode_content = True
    t0 = time.monotonic()
    try:
        rows = _parse_netex_streaming(resp.raw, logger)
    except Exception as exc:
        logger.error("Erreur parsing NeTEx : %s", exc, exc_info=True)
        return pd.DataFrame(columns=ICAR_BRONZE_COLUMNS)
    finally:
        resp.close()

    elapsed = time.monotonic() - t0
    logger.info("Parsing terminé en %.1fs — %d arrêts extraits au total", elapsed, len(rows))

    if not rows:
        logger.warning("Aucun arrêt extrait — vérifier la structure NeTEx")
        return pd.DataFrame(columns=ICAR_BRONZE_COLUMNS)

    # 3. Construction du DataFrame
    df = pd.DataFrame(rows)
    df["batch_ts"] = batch_ts

    # Typage propre
    df["latitude"]       = pd.to_numeric(df["latitude"],  errors="coerce")
    df["longitude"]      = pd.to_numeric(df["longitude"], errors="coerce")
    df["stop_id"]        = df["stop_id"].astype(str)
    df["stop_name"]      = df["stop_name"].astype(str)
    df["transport_mode"] = df["transport_mode"].astype(str)

    # 4. Filtre géographique Paris
    mask_paris = (
        df["latitude"].between(_PARIS_LAT_MIN, _PARIS_LAT_MAX) &
        df["longitude"].between(_PARIS_LON_MIN, _PARIS_LON_MAX)
    )
    df_paris = df[mask_paris].copy().reset_index(drop=True)
    logger.info(
        "Filtre géographique Paris : %d arrêts retenus / %d hors périmètre",
        len(df_paris), len(df) - len(df_paris),
    )

    if df_paris.empty:
        logger.warning("Aucun arrêt dans la bbox Paris — vérifier la reprojection")
        return pd.DataFrame(columns=ICAR_BRONZE_COLUMNS)

    # 5. Dédoublonnage sur stop_id
    n_before = len(df_paris)
    df_paris = df_paris.drop_duplicates(subset=["stop_id"], keep="first").reset_index(drop=True)
    if len(df_paris) < n_before:
        logger.info(
            "Dédoublonnage : %d → %d arrêts (supprimé %d doublons)",
            n_before, len(df_paris), n_before - len(df_paris),
        )

    # Ordre final des colonnes
    df_paris = df_paris[ICAR_BRONZE_COLUMNS]

    # 6. Limitation optionnelle
    if limit is not None:
        df_paris = df_paris.head(limit)
        logger.info("Limite appliquée : %d arrêts", len(df_paris))

    # 7. Statistiques par mode
    mode_counts = df_paris["transport_mode"].value_counts()
    logger.info("Répartition par mode de transport :")
    for mode, cnt in mode_counts.items():
        logger.info("  %-14s : %4d arrêts", mode, cnt)

    # 8. Sauvegarde
    if dry_run:
        logger.info("[DRY-RUN] Aperçu des 5 premiers arrêts :")
        logger.info("\n%s", df_paris.head().to_string(index=False))
        logger.info("[DRY-RUN] Fichier NON sauvegardé.")
    else:
        path = save_parquet(df_paris, source="icar", filename="part-0.parquet")
        logger.info("Bronze ICAR sauvegardé → %s (%d lignes)", path, len(df_paris))

    logger.info("=" * 60)
    logger.info("ICAR ingestion terminée — %d arrêts parisiens", len(df_paris))
    logger.info("=" * 60)

    return df_paris


# ---------------------------------------------------------------------------
# Point d'entrée CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Ingestion Bronze du référentiel ICAR (arrêts physiques IdF — NeTEx)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples :
  # Ingestion complète (~141 MB NeTEx, parsing ~60s)
  python -m src.ingestion.icar_referentiel

  # Aperçu sans écriture (200 arrêts parisiens)
  python -m src.ingestion.icar_referentiel --dry-run --limit 200

  # Test rapide : limite 500 arrêts, sauvegarde
  python -m src.ingestion.icar_referentiel --limit 500
""",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Affiche les données sans sauvegarder le Parquet",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Nombre maximum d'arrêts parisiens à retenir",
    )
    args = parser.parse_args()

    df_result = ingest_icar(limit=args.limit, dry_run=args.dry_run)

    if not df_result.empty:
        print(f"\n✅  {len(df_result)} arrêts parisiens ingérés")
        print(df_result.head(10).to_string(index=False))
        print(f"\nModes : {df_result['transport_mode'].value_counts().to_dict()}")
    else:
        print("\n⚠️  Aucun arrêt récupéré — consulter les logs pour le détail.")
