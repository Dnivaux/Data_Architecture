"""
/api/chantiers — Chantiers Paris en temps réel
================================================
Proxy de Paris Open Data : chantiers-a-paris
https://opendata.paris.fr/api/explore/v2.1/catalog/datasets/chantiers-a-paris/records
"""
from __future__ import annotations

import logging

import requests
from fastapi import APIRouter, HTTPException, Query

router = APIRouter(prefix="/chantiers", tags=["chantiers"])
logger = logging.getLogger("api.chantiers")

PARIS_OD_BASE = "https://opendata.paris.fr/api/explore/v2.1/catalog/datasets"
DATASET_ID = "chantiers-a-paris"
_TIMEOUT = 15


def _extract_coords(geo_point) -> tuple[float | None, float | None]:
    if isinstance(geo_point, dict):
        return geo_point.get("lat"), geo_point.get("lon")
    if isinstance(geo_point, (list, tuple)) and len(geo_point) >= 2:
        return float(geo_point[0]), float(geo_point[1])
    return None, None


@router.get(
    "/live",
    summary="Chantiers en cours à Paris (temps réel)",
    description=(
        "Retourne les chantiers actifs depuis Paris Open Data. "
        "Filtre optionnel par arrondissement."
    ),
)
def get_chantiers_live(
    arrondissement: int | None = Query(None, ge=1, le=20, description="Filtrer par arrondissement (1-20)"),
    limit: int = Query(300, ge=1, le=500),
) -> dict:
    url = f"{PARIS_OD_BASE}/{DATASET_ID}/records"
    params: dict = {"limit": limit, "timezone": "UTC"}
    if arrondissement is not None:
        params["where"] = f"c_ar={arrondissement}"

    try:
        resp = requests.get(url, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
    except requests.Timeout:
        raise HTTPException(status_code=504, detail="Timeout Paris Open Data")
    except requests.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Paris Open Data HTTP {exc.response.status_code if exc.response else '?'}",
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Erreur Paris Open Data : {exc}")

    data = resp.json()
    records = data.get("results", [])
    logger.info("Chantiers Paris OD : %d résultats (arr=%s)", len(records), arrondissement)

    chantiers = []
    for r in records:
        lat, lon = _extract_coords(r.get("geo_point_2d"))

        chantiers.append({
            "id":            (r.get("identifiant_dossier") or r.get("numero_dossier") or r.get("id") or ""),
            "titre":         (r.get("titredudossier") or r.get("libelle_du_chantier") or r.get("titre") or "Chantier"),
            "description":   (r.get("description") or r.get("libelle_synthese_arrete") or ""),
            "date_debut":    (r.get("debut_chantier") or r.get("date_debut_chantier") or r.get("date_debut")),
            "date_fin":      (r.get("fin_chantier") or r.get("date_fin_chantier") or r.get("date_fin")),
            "arrondissement": r.get("c_ar"),
            "adresse":       (r.get("adresse_du_chantier") or r.get("adresse") or ""),
            "statut":        (r.get("statut_dossier") or r.get("statut") or ""),
            "maitre_ouvrage": (r.get("maitre_ouvrage") or r.get("emetteur") or ""),
            "lat":           float(lat) if lat is not None else None,
            "lon":           float(lon) if lon is not None else None,
        })

    # Ne garder que les chantiers géolocalisés (utiles sur la carte)
    geolocated = [c for c in chantiers if c["lat"] is not None and c["lon"] is not None]

    return {
        "total":      data.get("total_count", len(chantiers)),
        "count":      len(geolocated),
        "chantiers":  geolocated,
    }
