"""
Open-Meteo Air Quality & Pollen — Bronze Ingestion
===================================================
Source  : Open-Meteo Air Quality API (CAMS Europe)
Endpoint: https://air-quality-api.open-meteo.com/v1/air-quality
API doc : https://open-meteo.com/en/docs/air-quality-api

Pourquoi Open-Meteo plutôt qu'Airparif ?
----------------------------------------
  • 100 % gratuit, AUCUNE clé API, aucune inscription (licence CC-BY 4.0)
  • Polluants réglementaires : PM2.5, PM10, NO2, O3, SO2, CO + European AQI
  • POLLEN (Europe, donc Paris) : aulne, bouleau, graminées, armoise,
    olivier, ambroisie — prévision sur ~4 jours (modèle CAMS)
  • Données actuelles + horaires + historiques (past_days) → vraie timeline
  • Une requête par centroïde d'arrondissement (lat/lon issus de boundaries)

Remplace l'ancien module Airparif (src/ingestion/air_quality.py) qui exigeait
AIRPARIF_API_KEY et ne fournissait pas le pollen.

Flow
----
1. Charge les centroïdes des 20 arrondissements depuis le Bronze boundaries.
2. Pour chaque arrondissement, GET current (AQI + polluants) + hourly (pollen).
3. Agrège le pollen en pic journalier (max sur 24h) par espèce.
4. Dérive un niveau European AQI 1-6 + un niveau de risque pollinique.
5. Persiste en Parquet partitionné par date d'exécution.

Bronze schema  (data/bronze/air_quality/date=YYYY-MM-DD/part-0.parquet)
-------------
arrondissement   int      1–20
commune_code     str      Code INSEE  e.g. '75114'
latitude         float    Centroïde
longitude        float
date_mesure      date     Date de la mesure (Europe/Paris)
european_aqi     float    Indice européen 0-100+ (0 = excellent)
aqi_level        int      Niveau dérivé 1-6 (1 = bon → 6 = extrêmement mauvais)
aqi_label        str      Libellé FR
indice_atmo_num  int      Alias de aqi_level (compat. rétro avec l'ancien scoring)
pm2_5            float    µg/m³
pm10             float    µg/m³
no2              float    µg/m³ (nitrogen_dioxide)
o3               float    µg/m³ (ozone)
so2              float    µg/m³ (sulphur_dioxide)
co               float    µg/m³ (carbon_monoxide)
pollen_alder     float    grains/m³ (pic journalier)
pollen_birch     float
pollen_grass     float
pollen_mugwort   float
pollen_olive     float
pollen_ragweed   float
pollen_total     float    Somme des 6 espèces
pollen_risk      str      Faible | Modéré | Élevé | Très élevé
ingested_at      datetime UTC
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from .base import build_session, get_logger, read_parquet, save_parquet

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ENDPOINT = "https://air-quality-api.open-meteo.com/v1/air-quality"
LOG_DIR = Path(__file__).parents[2] / "logs"

_CURRENT_VARS = [
    "european_aqi",
    "pm2_5",
    "pm10",
    "nitrogen_dioxide",
    "ozone",
    "sulphur_dioxide",
    "carbon_monoxide",
]
_POLLEN_VARS = [
    "alder_pollen",
    "birch_pollen",
    "grass_pollen",
    "mugwort_pollen",
    "olive_pollen",
    "ragweed_pollen",
]

# Centroïdes de secours si le Bronze boundaries est absent (lat, lon).
_FALLBACK_CENTROIDS: dict[int, tuple[float, float]] = {
    1: (48.8626, 2.3363),   2: (48.8679, 2.3417),   3: (48.8637, 2.3615),
    4: (48.8546, 2.3573),   5: (48.8448, 2.3501),   6: (48.8490, 2.3329),
    7: (48.8565, 2.3120),   8: (48.8726, 2.3125),   9: (48.8770, 2.3380),
    10: (48.8760, 2.3610),  11: (48.8590, 2.3790),  12: (48.8350, 2.4210),
    13: (48.8290, 2.3620),  14: (48.8330, 2.3270),  15: (48.8420, 2.2930),
    16: (48.8600, 2.2620),  17: (48.8870, 2.3070),  18: (48.8920, 2.3490),
    19: (48.8870, 2.3820),  20: (48.8640, 2.3980),
}

BRONZE_COLUMNS = [
    "arrondissement", "commune_code", "latitude", "longitude", "date_mesure",
    "european_aqi", "aqi_level", "aqi_label", "indice_atmo_num",
    "pm2_5", "pm10", "no2", "o3", "so2", "co",
    "pollen_alder", "pollen_birch", "pollen_grass",
    "pollen_mugwort", "pollen_olive", "pollen_ragweed",
    "pollen_total", "pollen_risk",
    "ingested_at",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _aqi_to_level(aqi: float | None) -> tuple[int | None, str | None]:
    """Convertit l'European AQI (EEA) en niveau 1-6 + libellé FR."""
    if aqi is None:
        return None, None
    bands = [
        (20,  1, "Bon"),
        (40,  2, "Moyen"),
        (60,  3, "Dégradé"),
        (80,  4, "Mauvais"),
        (100, 5, "Très mauvais"),
    ]
    for threshold, level, label in bands:
        if aqi <= threshold:
            return level, label
    return 6, "Extrêmement mauvais"


def _pollen_risk(total: float) -> str:
    """Niveau de risque pollinique à partir du total (grains/m³)."""
    if total < 10:
        return "Faible"
    if total < 50:
        return "Modéré"
    if total < 150:
        return "Élevé"
    return "Très élevé"


def _load_centroids(logger) -> dict[int, tuple[float, float]]:
    """Charge les centroïdes par arrondissement depuis le Bronze boundaries."""
    df = read_parquet("boundaries")
    if df.empty or "centroid_lat" not in df.columns:
        logger.warning("Boundaries absentes — utilisation des centroïdes de secours")
        return _FALLBACK_CENTROIDS

    centroids: dict[int, tuple[float, float]] = {}
    for _, row in df.iterrows():
        try:
            arr = int(row["arrondissement"])
            lat = float(row["centroid_lat"])
            lon = float(row["centroid_lon"])
            if 1 <= arr <= 20 and lat and lon:
                centroids[arr] = (lat, lon)
        except (ValueError, TypeError):
            continue

    # Compléter les manquants avec le fallback
    for arr, coords in _FALLBACK_CENTROIDS.items():
        centroids.setdefault(arr, coords)
    return centroids


def _safe_max(values: list) -> float:
    """Max d'une liste en ignorant None/NaN. 0.0 si vide."""
    nums = [v for v in values if isinstance(v, (int, float)) and v is not None]
    return float(max(nums)) if nums else 0.0


def _fetch_one(session, logger, arr: int, lat: float, lon: float,
               ingested_at: datetime) -> dict | None:
    """Récupère AQI + polluants + pollen pour un arrondissement."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": ",".join(_CURRENT_VARS),
        "hourly": ",".join(_POLLEN_VARS),
        "timezone": "Europe/Paris",
        "forecast_days": 1,
    }
    resp = session.get(ENDPOINT, params=params)
    if resp.status_code != 200:
        logger.warning("Arr %02d : HTTP %d — %s", arr, resp.status_code, resp.text[:150])
        return None

    data = resp.json()
    cur = data.get("current", {}) or {}
    hourly = data.get("hourly", {}) or {}

    aqi = cur.get("european_aqi")
    level, label = _aqi_to_level(aqi)

    # Pic journalier de pollen par espèce
    pollen = {var: _safe_max(hourly.get(var, [])) for var in _POLLEN_VARS}
    pollen_total = round(sum(pollen.values()), 1)

    return {
        "arrondissement":  arr,
        "commune_code":    f"751{arr:02d}",
        "latitude":        lat,
        "longitude":       lon,
        "date_mesure":     (cur.get("time") or "")[:10] or date.today().isoformat(),
        "european_aqi":    float(aqi) if aqi is not None else None,
        "aqi_level":       level,
        "aqi_label":       label,
        "indice_atmo_num": level,  # alias rétro-compat avec l'ancien module Airparif
        "pm2_5":           cur.get("pm2_5"),
        "pm10":            cur.get("pm10"),
        "no2":             cur.get("nitrogen_dioxide"),
        "o3":              cur.get("ozone"),
        "so2":             cur.get("sulphur_dioxide"),
        "co":              cur.get("carbon_monoxide"),
        "pollen_alder":    pollen["alder_pollen"],
        "pollen_birch":    pollen["birch_pollen"],
        "pollen_grass":    pollen["grass_pollen"],
        "pollen_mugwort":  pollen["mugwort_pollen"],
        "pollen_olive":    pollen["olive_pollen"],
        "pollen_ragweed":  pollen["ragweed_pollen"],
        "pollen_total":    pollen_total,
        "pollen_risk":     _pollen_risk(pollen_total),
        "ingested_at":     ingested_at,
    }


# ---------------------------------------------------------------------------
# Point d'entrée public
# ---------------------------------------------------------------------------

def ingest() -> pd.DataFrame:
    """Ingère la qualité de l'air + pollen pour les 20 arrondissements parisiens."""
    logger = get_logger("open_meteo_air", LOG_DIR)
    ingested_at = datetime.now(timezone.utc)
    run_date = date.today().isoformat()

    centroids = _load_centroids(logger)
    session = build_session(retries=3, backoff_factor=0.5, timeout=20)

    logger.info(
        "Open-Meteo air+pollen — ingestion de %d arrondissements (run-date=%s)",
        len(centroids), run_date,
    )

    records: list[dict] = []
    for arr in sorted(centroids):
        lat, lon = centroids[arr]
        try:
            row = _fetch_one(session, logger, arr, lat, lon, ingested_at)
            if row:
                records.append(row)
        except Exception as exc:  # noqa: BLE001
            logger.error("Arr %02d : échec récupération — %s", arr, exc)

    if not records:
        logger.error("Aucune donnée Open-Meteo récupérée — Bronze non créé")
        return pd.DataFrame(columns=BRONZE_COLUMNS)

    df = pd.DataFrame(records)[BRONZE_COLUMNS]
    df["date_mesure"] = pd.to_datetime(df["date_mesure"], errors="coerce").dt.date
    df["aqi_level"] = pd.to_numeric(df["aqi_level"], errors="coerce").astype("Int64")
    df["indice_atmo_num"] = df["aqi_level"]

    path = save_parquet(
        df, source="air_quality",
        partition_col="date", partition_value=run_date,
        filename="part-0.parquet",
    )
    logger.info(
        "Open-Meteo air+pollen terminé — %d arrondissements → %s "
        "(AQI moyen=%.0f, pollen total moyen=%.0f)",
        len(df), path, df["european_aqi"].mean(), df["pollen_total"].mean(),
    )
    return df


if __name__ == "__main__":
    result = ingest()
    if not result.empty:
        cols = ["arrondissement", "european_aqi", "aqi_label",
                "pm2_5", "no2", "pollen_grass", "pollen_total", "pollen_risk"]
        print("\n--- Aperçu Bronze Open-Meteo (air + pollen) ---")
        print(result[cols].to_string(index=False))
        print(f"\nShape : {result.shape}")
