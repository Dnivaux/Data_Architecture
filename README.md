# Urban Data Explorer — Full Data Stack

A complete geospatial data platform for Paris housing & lifestyle analysis.

**Architecture**: Bronze (raw) → Silver (processed) → Gold (API-ready) → FastAPI REST → MapLibre GL JS frontend

---

## Architecture & Data Flow

```
APIs (DVF, INSEE, Airparif, OSM, etc.)
  ↓ [Bronze Layer]
  Raw Parquet files (ingestion/*)
  ↓ [Silver Layer] 
  Spatial processing + Scoring
  – Animate score (POI density)
  – Calm score (crime, air quality inverse)
  – Financial accessibility score
  ↓ [Gold Layer]
  API-ready tables (denormalized, pre-aggregated)
  ↓ [FastAPI]
  REST endpoints (/api/scores, /api/poi, /api/prices, /api/comparison)
  ↓ [MapLibre GL JS]
  Interactive map with choroplèthe, POI layers, timeline, comparison mode
```

## Project Structure

```
Data_Architecture/
├── data/
│   ├── bronze/             Raw data (partitioned by source & date)
│   │   ├── dvf/
│   │   ├── osm/
│   │   ├── boundaries/
│   │   ├── revenus/, air_quality/, crime/  (stubs)
│   ├── silver/             Processed data (scores, aggregations)
│   │   ├── scores_by_arrondissement.parquet
│   │   ├── prices_by_arrondissement_year.parquet
│   │   └── amenities_by_arrondissement.parquet
│   └── gold/               API-ready tables
│       ├── arrondissement_summary.parquet
│       ├── poi_catalog.parquet
│       └── price_timeline.parquet
├── src/
│   ├── ingestion/          Bronze layer (API ↔ raw Parquet)
│   │   ├── base.py
│   │   ├── dvf.py, osm.py, boundaries.py, revenus.py, air_quality.py, crime.py
│   ├── silver/             Silver layer (spatial processing + scoring)
│   │   ├── scoring.py      ArrondissementScorer (Animé, Calme, Accessibilité)
│   │   └── aggregation.py  Aggregate Bronze → Silver
│   └── gold/               Gold layer (API optimizations)
│       └── build.py        Build API-ready tables
├── api/                    FastAPI REST backend
│   ├── main.py
│   ├── schemas.py          Pydantic models
│   ├── dependencies.py     Cache, data loading
│   └── routers/
│       ├── scores.py       GET /api/scores/*
│       ├── poi.py          GET /api/poi/*
│       ├── prices.py       GET /api/prices/* (timeline)
│       └── comparison.py   GET /api/comparison/*
├── logs/
├── main.py                 Bronze layer pipeline
├── pipeline.py             Full orchestrator (Bronze → Silver → Gold)
└── requirements.txt
```

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Ingest Bronze layer (raw data from APIs)

```bash
python main.py
```

Options:
```bash
# Specific sources only
python main.py --sources dvf osm boundaries

# DVF with custom date range
python main.py --sources dvf --date-min 2023-01-01 --date-max 2023-12-31

# Dry run (no HTTP calls)
python main.py --dry-run
```

### 3. Build Silver + Gold layers (spatial processing)

```bash
python pipeline.py
```

This runs:
- Silver layer: Compute livability scores, aggregate by arrondissement
- Gold layer: Prepare final API-ready tables

### 4. Start the API

```bash
python -m api.main
```

API runs on `http://localhost:8000`
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

### 5. Test endpoints

```bash
# All livability scores
curl http://localhost:8000/api/scores/all

# Specific arrondissement (1-20)
curl http://localhost:8000/api/scores/3

# All POI (bars, nightclubs, parks)
curl http://localhost:8000/api/poi/

# POI by category
curl http://localhost:8000/api/poi/by-category/bar

# Price timeline
curl http://localhost:8000/api/prices/timeline

# Compare two arrondissements
curl "http://localhost:8000/api/comparison/?a=3&b=5"
```

---

## Data sources

| Source | Status | Partition key | Notes |
|---|---|---|---|
| **DVF** | ✅ Implemented | `date=YYYY-MM-DD` | One file per arrondissement per run-date |
| **OSM** | ✅ Implemented | `amenity_type=bar\|nightclub\|park` | Nodes + way centroids |
| **Boundaries** | ✅ Implemented | none | Also writes raw GeoJSON |
| **Revenus** | 🔧 Stub | `year=YYYY` | Requires free INSEE API key |
| **Air Quality** | 🔧 Stub | `date=YYYY-MM-DD` | Requires Airparif ArcGIS layer URL |
| **Crime** | 🔧 Stub | `year=YYYY` | Download SSMSI CSV from data.gouv.fr |

---

## Bronze layer schema

Every source produces a Parquet file with an `ingested_at` UTC timestamp column.
All geographic data is normalised to `latitude` / `longitude` (WGS84 / EPSG:4326).

### DVF (`data/bronze/dvf/`)

| Column | Type | Description |
|---|---|---|
| `id_mutation` | str | Unique transaction id |
| `date_mutation` | date | Sale date |
| `valeur_fonciere` | float | Sale price (€) |
| `adresse_numero` | str | Street number |
| `adresse_nom_voie` | str | Street name |
| `code_postal` | str | Postal code |
| `code_commune` | str | INSEE commune code |
| `type_local` | str | Property type (Appartement, Maison…) |
| `surface_reelle_bati` | float | Built area (m²) |
| `surface_terrain` | float | Land area (m²) |
| `nombre_pieces_principales` | int | Room count |
| `latitude` | float | WGS84 |
| `longitude` | float | WGS84 |
| `nature_mutation` | str | e.g. "Vente" |
| `ingested_at` | datetime | UTC ingestion timestamp |

### OSM (`data/bronze/osm/`)

| Column | Type | Description |
|---|---|---|
| `osm_id` | int | OSM element id |
| `osm_type` | str | "node" or "way" |
| `amenity_type` | str | bar / nightclub / park |
| `name` | str | OSM name tag |
| `latitude` | float | WGS84 |
| `longitude` | float | WGS84 |
| `opening_hours` | str | OSM opening_hours tag |
| `wheelchair` | str | OSM wheelchair tag |
| `tags` | str | Full JSON-encoded tag dict |
| `ingested_at` | datetime | UTC ingestion timestamp |

### Boundaries (`data/bronze/boundaries/`)

| Column | Type | Description |
|---|---|---|
| `arrondissement` | int | 1–20 |
| `c_ar` | str | Short code |
| `c_arinsee` | str | Full INSEE code (e.g. "75101") |
| `l_ar` | str | Label (e.g. "1er arrondissement") |
| `surface_ha` | float | Area in hectares |
| `centroid_lat` | float | Polygon centroid latitude |
| `centroid_lon` | float | Polygon centroid longitude |
| `geometry_wkt` | str | WKT polygon string |
| `ingested_at` | datetime | UTC ingestion timestamp |

---

## Updating the data

| Frequency | Command |
|---|---|
| **Daily** (DVF, OSM) | `python main.py --sources dvf osm` |
| **Annual** (Revenus, Crime) | `python main.py --sources revenus crime` |
| **One-time / on change** (Boundaries) | `python main.py --sources boundaries` |

To schedule daily ingestion (Windows Task Scheduler):

```
Action: python C:\...\Data_Architecture\main.py --sources dvf osm
Trigger: Daily at 06:00
```

Or with cron (Linux/macOS):

```cron
0 6 * * * cd /path/to/Data_Architecture && python main.py --sources dvf osm
```

---

## API Endpoints Reference

All endpoints return JSON and support CORS.

### Livability Scores (`/api/scores/`)

**GET /api/scores/all**
- Returns all scores for all 20 arrondissements
- Response: Array of `ArrondissementScore`

**GET /api/scores/{arrondissement}**
- Path: `arrondissement` (1-20)
- Response: Single `ArrondissementScore`

Response schema:
```json
{
  "arrondissement": 3,
  "anime_score": 75.2,
  "calme_score": 62.5,
  "accessibilite_score": 58.3,
  "bar_count": 45,
  "nightclub_count": 8,
  "park_count": 3,
  "median_price": 650000,
  "social_housing_pct": 18.5
}
```

### Points of Interest (`/api/poi/`)

**GET /api/poi/**
- Query: `category` (optional: "bar", "nightclub", "park")
- Response: Array of `POI`

**GET /api/poi/by-category/{category}**
- Path: `category` ("bar" | "nightclub" | "park")
- Response: Array of `POI`

Response schema:
```json
{
  "id": 123456,
  "type": "node",
  "category": "bar",
  "name": "Le Marais Café",
  "lat": 48.8566,
  "lon": 2.3522,
  "hours": "10:00-02:00",
  "wheelchair_accessible": "yes"
}
```

### Price Timeline (`/api/prices/`)

**GET /api/prices/timeline**
- Query: `arrondissement` (optional: 1-20)
- Response: Array of `PriceTimeline` (2014-2023)

**GET /api/prices/arrondissement/{arrondissement}**
- Path: `arrondissement` (1-20)
- Response: Array of `PriceTimeline` for that arrondissement

Response schema:
```json
{
  "arrondissement": 3,
  "year": 2023,
  "median_price": 650000,
  "transaction_count": 245
}
```

### Comparison (`/api/comparison/`)

**GET /api/comparison/**
- Query: `a` (1-20), `b` (1-20)
- Response: `ArrondissementComparison`

Response schema:
```json
{
  "arrond_a": 3,
  "arrond_b": 5,
  "scores_a": { /* ArrondissementScore */ },
  "scores_b": { /* ArrondissementScore */ },
  "price_diff": -50000,
  "livability_diff": 3.2
}
```

---

## Frontend Integration (MapLibre GL JS)

### Recommended Map Layers

1. **Choroplèthe by Arrondissement**
   - Data source: `/api/scores/all`
   - Color by: `anime_score` or `calme_score` or `accessibilite_score`
   - Interaction: Click to show pop-up with full details

2. **POI Layer**
   - Data source: `/api/poi/` (with category filter)
   - Toggle by type: bars, nightclubs, parks
   - Interaction: Click for amenity details (hours, wheelchair access, etc.)

3. **Price Evolution Timeline**
   - Data source: `/api/prices/timeline`
   - Timeline slider (2014-2023)
   - Update choroplèthe colors as year changes

4. **Comparison Mode**
   - Toggle: Select two arrondissements
   - Data source: `/api/comparison/?a=X&b=Y`
   - Display side-by-side stats and highlight both geometries

### Example MapLibre Setup

```javascript
import MaplibreGL from 'maplibre-gl';

const map = new MaplibreGL.Map({
  container: 'map',
  style: 'https://demotiles.maplibre.org/style.json',
  center: [2.3522, 48.8566], // Paris
  zoom: 11,
});

// Fetch scores and add choroplèthe layer
fetch('http://localhost:8000/api/scores/all')
  .then(r => r.json())
  .then(scores => {
    // Build GeoJSON FeatureCollection from scores + boundaries
    // Add to map as fill layer with color property
  });

// Fetch POI and add marker layer
fetch('http://localhost:8000/api/poi/')
  .then(r => r.json())
  .then(poi => {
    // Convert to GeoJSON points
    // Add as symbol layer with cluster option
  });
```

---

## Completing the Stubs

### Revenus (INSEE)
1. Register for a free API key at https://api.insee.fr
2. Set env var: `INSEE_API_KEY=your_key`
3. Implement `src/ingestion/revenus.py` — query IRIS codes for dept=75

### Air Quality (Airparif)
1. Browse layers at https://data-airparif-asso.opendata.arcgis.com/
2. Copy the target FeatureServer URL into `src/ingestion/air_quality.py`
3. Use ArcGIS `query` endpoint with `f=geojson&outFields=*&resultOffset=N`

### Crime (SSMSI)
1. Download CSV from https://www.data.gouv.fr (search "SSMSI base communale")
2. Place in `data/raw/crime/` and update `src/ingestion/crime.py` to parse it
