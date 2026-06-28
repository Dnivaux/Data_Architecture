# Urban Data Explorer

Plateforme d'ingénierie de données pour **explorer et comparer les dynamiques du logement et de la qualité de vie à Paris**, par arrondissement et à la maille IRIS (~992 zones).

Chaîne complète : sources ouvertes → architecture **Medallion (Bronze → Silver → Gold)** → stockage **PostgreSQL/PostGIS** (+ MongoDB) → **API FastAPI** → **dashboard React/Leaflet**. Orchestration **Airflow**.

---

## Architecture

```
Sources ouvertes (DVF, OSM, INSEE, ARCEP, Open-Meteo, Bruitparif, Vélib'…)
        │  ingestion (src/ingestion/)
        ▼
   BRONZE  data/bronze/   (Parquet, donnée brute partitionnée)
        │  src/silver/  (nettoyage, jointures spatiales, scores)
        ▼
   SILVER  data/silver/   (tables nettoyées + scores 0-100)
        │  src/gold/
        ▼
   GOLD    PostgreSQL/PostGIS (tables gold_*) + MongoDB (documents)
        │  api/  (FastAPI, lecture seule, sécurisée)
        ▼
   FRONTEND  frontend/ (React + Vite + Leaflet + Recharts)
```

## Prérequis

- **Docker** + **Docker Compose**
- **Python 3.11+** (chemin local / pipeline)
- **Node 18+** (frontend)

## Configuration

```bash
cp .env.example .env          # adapter si besoin (clés API, etc.)
# frontend/.env contient déjà VITE_API_KEY=dev-key-123 (clé de dev)
```

---

## Démarrage — Option A (recommandée) : Docker + Airflow

Orchestration de production : ingestion parallèle des sources, retries, supervision via l'UI Airflow.

```bash
# 1. Démarrer toute la stack (postgres, mongo, api, airflow)
docker compose up -d

# 2. Ouvrir Airflow  →  http://localhost:8080   (login : airflow / airflow)
#    Activer puis déclencher le DAG « urban_data_pipeline » (bouton ▶).
#    Il ingère toutes les sources → Silver → Gold → export PostgreSQL.
#    (~quelques minutes ; une source indisponible ne bloque pas le pipeline.)

# 3. Lancer le frontend (non conteneurisé)
cd frontend && npm install && npm run dev
```

- API + Swagger : http://localhost:8000/docs
- Dashboard : http://localhost:5173

## Démarrage — Option B (local rapide, sans Airflow)

```bash
# 1. Bases de données
docker compose up -d postgres mongo

# 2. Pipeline complet en UNE commande
#    (Bronze : sources de base + indicateurs + mobilité → Silver → Gold → export PG)
python -m pip install -r requirements.txt
python pipeline.py

# 3. API
python -m uvicorn api.main:app --reload

# 4. Frontend (autre terminal)
cd frontend && npm install && npm run dev
```

> ⚠️ **Ne pas** lancer uniquement `docker compose up` (dashboard vide : aucune donnée chargée) ni uniquement `python main.py` (ingestion de base sans Silver/Gold). Utiliser l'option A **ou** B ci-dessus.

## Vérifier que tout fonctionne

```bash
curl http://localhost:8000/health        # database_connected: true + gold_tables_found: [...]
curl http://localhost:8000/api/scores/all  # 20 arrondissements avec scores
```

---

## Variables d'environnement (extrait)

| Variable | Rôle | Défaut (dev) |
|---|---|---|
| `DATABASE_URL` | PostgreSQL (tables Gold) | `postgresql://postgres:postgres@localhost:5432/urbandata` |
| `MONGODB_URI` | MongoDB (documents) | vide = désactivé |
| `API_KEYS` | clés d'API (`X-API-Key`) ; vide = auth désactivée | `dev-key-123` |
| `APP_ENV` | `development` / `production` (prod = auth obligatoire + /docs masqué) | `development` |
| `ALLOWED_ORIGINS` | CORS (front Vite) | `http://localhost:5173` |
| `VITE_API_KEY` | clé envoyée par le front (dans `frontend/.env`) | `dev-key-123` |

## Structure du dépôt

```
src/ingestion/   sources Bronze (dvf, osm, boundaries, iris, revenus, …)
src/silver/      nettoyage, agrégations, scoring (arrondissement + IRIS)
src/gold/        construction des tables gold_* + export PostgreSQL
src/nosql/       export MongoDB
api/             FastAPI (routers par domaine + sécurité)
frontend/        dashboard React/Vite/Leaflet
dags/            DAGs Airflow (pipeline complet + rafraîchissements)
notebooks/EDA/   exploration par source
data/{bronze,silver,gold}/   couches Medallion (Parquet)
```

## Notes pour un repreneur

- **Source `crime` (best-effort)** : retirée du *score* de tranquillité (conservée comme métrique détaillée). Si son ingestion échoue, le pipeline continue (le DAG utilise `trigger_rule="all_done"` sur Silver).
- **Scores normalisés par percentile** : un score de `0` signifie « dernier du classement », pas « valeur nulle ». Un même territoire peut être à 0 en maille arrondissement (sur 20) et élevé en maille IRIS (sur ~992) — voir la note v5 dans `src/silver/scoring.py`.
