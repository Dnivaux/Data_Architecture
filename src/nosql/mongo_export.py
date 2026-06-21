"""
Export NoSQL — MongoDB (couche document)
========================================
Répond au critère RNCP C1.2 : « Concevoir et développer une base de données
non-relationnelle ... mise à disposition des données semi-structurées et non
structurées » + « Le choix des technologies NoSQL est justifié en fonction des
types de données et des besoins analytiques ».

Pourquoi MongoDB (document store) ici ?
---------------------------------------
PostgreSQL (relationnel) sert les agrégats Gold tabulaires (1 ligne =
1 arrondissement). Mais une partie des données est **semi-structurée** et se
prête mal à un schéma figé :

  • POI OSM : tags hétérogènes et creux (opening_hours, wheelchair, brand…),
    variables d'un point à l'autre → document JSON par POI.
  • Mesures air + pollen : structure imbriquée (6 espèces de pollen + 7
    polluants + métadonnées) qui évolue dans le temps → document par relevé.
  • Profils d'arrondissement : objet riche imbriqué (scores + métriques +
    géométrie) consommable tel quel par le front sans jointures.

MongoDB offre un schéma flexible, l'indexation géospatiale (2dsphere) et des
requêtes analytiques (aggregation pipeline) adaptées à ces formes.

Collections produites (base `urbandata`)
-----------------------------------------
  poi                    1 doc / POI OSM (index 2dsphere sur la localisation)
  air_quality_readings   1 doc / arrondissement / relevé (air + pollen imbriqués)
  arrondissement_profiles 1 doc / arrondissement (profil complet imbriqué)

Configuration
-------------
  MONGODB_URI : URI de connexion (ex. mongodb://localhost:27017)
                → si absent, l'export est ignoré proprement (comme PG hors ligne).
  MONGODB_DB  : nom de la base (défaut : urbandata)

Usage
-----
  python -m src.nosql.mongo_export            # export complet
  python -m src.nosql.mongo_export --dry-run  # vérifie la connexion sans écrire
"""
from __future__ import annotations

import argparse
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from src.ingestion.base import get_logger, read_parquet

load_dotenv(Path(__file__).parents[2] / ".env")

LOG_DIR = Path(__file__).parents[2] / "logs"
GOLD_ROOT = Path(__file__).parents[2] / "data" / "gold"
MONGODB_DB = os.environ.get("MONGODB_DB", "urbandata")


def _clean(value):
    """Convertit NaN/NaT en None pour des documents JSON propres."""
    if isinstance(value, float) and math.isnan(value):
        return None
    if value is pd.NaT:
        return None
    return value


def _records(df: pd.DataFrame) -> list[dict]:
    """DataFrame → liste de dicts nettoyés (NaN → None)."""
    return [{k: _clean(v) for k, v in row.items()} for row in df.to_dict("records")]


def _load_gold(filename: str) -> pd.DataFrame:
    path = GOLD_ROOT / filename
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path, engine="pyarrow")


def export_all(uri: str | None = None, dry_run: bool = False) -> dict[str, int]:
    """Exporte les collections NoSQL vers MongoDB. Retourne {collection: nb_docs}."""
    logger = get_logger("mongo_export", LOG_DIR)
    uri = uri or os.environ.get("MONGODB_URI")

    if not uri:
        logger.warning(
            "MONGODB_URI non défini — export NoSQL ignoré. "
            "Définir MONGODB_URI=mongodb://localhost:27017 pour activer."
        )
        return {}

    try:
        from pymongo import GEOSPHERE, MongoClient
    except ImportError:
        logger.error("pymongo non installé — `pip install pymongo`. Export NoSQL ignoré.")
        return {}

    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=3000)
        client.admin.command("ping")
        logger.info("Connexion MongoDB OK : %s", uri.split("@")[-1])
    except Exception as exc:
        logger.error("Connexion MongoDB échouée : %s", exc)
        return {}

    db = client[MONGODB_DB]
    results: dict[str, int] = {}
    now = datetime.now(timezone.utc)

    # --- 1. POI (semi-structuré, géolocalisé) ---
    poi = _load_gold("poi_catalog.parquet")
    if not poi.empty:
        docs = []
        for r in _records(poi):
            lat, lon = r.get("lat"), r.get("lon")
            if lat is not None and lon is not None:
                r["location"] = {"type": "Point", "coordinates": [lon, lat]}
            r["_synced_at"] = now
            docs.append(r)
        if not dry_run:
            db.poi.drop()
            db.poi.insert_many(docs)
            db.poi.create_index([("location", GEOSPHERE)])
            db.poi.create_index("category")
        results["poi"] = len(docs)
        logger.info("poi : %d documents%s", len(docs), " (dry-run)" if dry_run else "")

    # --- 2. Mesures air + pollen (structure imbriquée) ---
    air = read_parquet("air_quality")
    if not air.empty:
        docs = []
        for r in _records(air):
            doc = {
                "arrondissement": r.get("arrondissement"),
                "commune_code": r.get("commune_code"),
                "date_mesure": str(r.get("date_mesure")),
                "air_quality": {
                    "european_aqi": r.get("european_aqi"),
                    "aqi_level": r.get("aqi_level"),
                    "aqi_label": r.get("aqi_label"),
                    "pollutants": {
                        "pm2_5": r.get("pm2_5"), "pm10": r.get("pm10"),
                        "no2": r.get("no2"), "o3": r.get("o3"),
                        "so2": r.get("so2"), "co": r.get("co"),
                    },
                },
                "pollen": {
                    "alder": r.get("pollen_alder"), "birch": r.get("pollen_birch"),
                    "grass": r.get("pollen_grass"), "mugwort": r.get("pollen_mugwort"),
                    "olive": r.get("pollen_olive"), "ragweed": r.get("pollen_ragweed"),
                    "total": r.get("pollen_total"), "risk": r.get("pollen_risk"),
                },
                "_synced_at": now,
            }
            docs.append(doc)
        if not dry_run:
            db.air_quality_readings.drop()
            db.air_quality_readings.insert_many(docs)
            db.air_quality_readings.create_index([("arrondissement", 1), ("date_mesure", -1)])
        results["air_quality_readings"] = len(docs)
        logger.info("air_quality_readings : %d documents%s", len(docs), " (dry-run)" if dry_run else "")

    # --- 3. Profils d'arrondissement (document riche imbriqué) ---
    summary = _load_gold("arrondissement_summary.parquet")
    if not summary.empty:
        docs = _records(summary)
        for d in docs:
            d["_id"] = int(d["arrondissement"])
            d["_synced_at"] = now
        if not dry_run:
            db.arrondissement_profiles.drop()
            db.arrondissement_profiles.insert_many(docs)
        results["arrondissement_profiles"] = len(docs)
        logger.info("arrondissement_profiles : %d documents%s", len(docs), " (dry-run)" if dry_run else "")

    client.close()
    logger.info("Export MongoDB terminé : %s", results)
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export NoSQL (MongoDB) des données Gold")
    parser.add_argument("--dry-run", action="store_true", help="Vérifie la connexion sans écrire")
    parser.add_argument("--uri", help="URI MongoDB (surcharge MONGODB_URI)")
    args = parser.parse_args()

    res = export_all(uri=args.uri, dry_run=args.dry_run)
    print(json.dumps(res, indent=2, ensure_ascii=False))
