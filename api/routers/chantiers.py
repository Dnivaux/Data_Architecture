"""
/api/chantiers — Chantiers Paris en temps réel
================================================
Proxy de Paris Open Data : chantiers-a-paris

Champs réels du dataset (vérifiés sur l'API) :
  num_emprise, cp_arrondissement, date_debut, date_fin,
  chantier_categorie, moa_principal, surface, chantier_synthese,
  localisation_detail, geo_point_2d
"""
from __future__ import annotations

import logging

import requests
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/chantiers", tags=["chantiers"])
logger = logging.getLogger("api.chantiers")

PARIS_OD_BASE = "https://opendata.paris.fr/api/explore/v2.1/catalog/datasets"
DATASET_ID = "chantiers-a-paris"
_TIMEOUT = 20


def _extract_coords(geo_point) -> tuple[float | None, float | None]:
    if isinstance(geo_point, dict):
        return geo_point.get("lat"), geo_point.get("lon")
    if isinstance(geo_point, (list, tuple)) and len(geo_point) >= 2:
        return float(geo_point[0]), float(geo_point[1])
    return None, None


def _cp_to_arrondissement(cp: str | None) -> int | None:
    """Convertit '75012' → 12, '75001' → 1."""
    if not cp:
        return None
    s = str(cp).strip()
    if s.startswith("750") and len(s) == 5:
        try:
            n = int(s[3:])
            return n if 1 <= n <= 20 else None
        except ValueError:
            pass
    return None


@router.get(
    "/live",
    summary="Chantiers en cours à Paris (temps réel)",
    description=(
        "Retourne les chantiers actifs depuis Paris Open Data. "
        "Filtre optionnel par arrondissement."
    ),
)
def get_chantiers_live(
    arrondissement: int | None = Query(None, ge=1, le=20),
    limit: int = Query(100, ge=1, le=100),
) -> dict:
    url = f"{PARIS_OD_BASE}/{DATASET_ID}/records"
    params: dict = {"limit": limit, "timezone": "UTC"}

    # Filtre par code postal arrondissement (ex: 75012 pour le 12e)
    if arrondissement is not None:
        cp = f"750{str(arrondissement).zfill(2)}"
        params["where"] = f'cp_arrondissement="{cp}"'

    logger.info("Chantiers → %s params=%s", url, params)

    try:
        resp = requests.get(url, params=params, timeout=_TIMEOUT)
        if not resp.ok:
            # Log le corps de la réponse pour debug
            logger.error("Paris OD HTTP %d — %s", resp.status_code, resp.text[:300])
            raise HTTPException(
                status_code=502,
                detail=f"Paris Open Data HTTP {resp.status_code} : {resp.text[:200]}",
            )
    except requests.Timeout:
        raise HTTPException(status_code=504, detail="Timeout Paris Open Data (>20s)")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Erreur requête chantiers : %s", exc)
        raise HTTPException(status_code=502, detail=f"Erreur réseau : {exc}")

    data = resp.json()
    records = data.get("results", [])
    logger.info("Chantiers Paris OD : %d résultats (arr=%s)", len(records), arrondissement)

    chantiers = []
    for r in records:
        lat, lon = _extract_coords(r.get("geo_point_2d"))
        arr = _cp_to_arrondissement(r.get("cp_arrondissement"))

        chantiers.append({
            "id":            r.get("num_emprise") or r.get("chantier_cite_id") or "",
            "titre":         r.get("chantier_synthese") or r.get("chantier_categorie") or "Chantier",
            "categorie":     r.get("chantier_categorie") or "",
            "description":   r.get("localisation_detail") or r.get("localisation_stationnement") or "",
            "date_debut":    r.get("date_debut"),
            "date_fin":      r.get("date_fin"),
            "arrondissement": arr,
            "adresse":       r.get("cp_arrondissement") or "",
            "maitre_ouvrage": r.get("moa_principal") or "",
            "surface":       r.get("surface"),
            "lat":           float(lat) if lat is not None else None,
            "lon":           float(lon) if lon is not None else None,
        })

    geolocated = [c for c in chantiers if c["lat"] is not None and c["lon"] is not None]
    logger.info("Chantiers géolocalisés : %d / %d", len(geolocated), len(chantiers))

    return {
        "total":     data.get("total_count", len(chantiers)),
        "count":     len(geolocated),
        "chantiers": geolocated,
    }
