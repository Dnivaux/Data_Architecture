# Urban Data Explorer — Bronze Layer

Ingestion pipeline for the Paris housing & lifestyle data platform.
Raw data is fetched from public APIs and stored as partitioned Parquet files
in `data/bronze/`.

---

## Project structure

```
Data_Architecture/
├── data/
│   └── bronze/
│       ├── dvf/            date=YYYY-MM-DD/arrond_XX.parquet
│       ├── osm/            amenity_type=bar|nightclub|park/part-0.parquet
│       ├── boundaries/     part-0.parquet + arrondissements.geojson
│       ├── revenus/        (stub — pending INSEE API key)
│       ├── air_quality/    (stub — pending Airparif layer URL)
│       └── crime/          (stub — pending SSMSI CSV download)
├── src/
│   └── ingestion/
│       ├── base.py         Shared utilities (retry, logging, Parquet I/O)
│       ├── dvf.py          IGN Apicarto – property transactions
│       ├── osm.py          Overpass API – bars, parks, nightclubs
│       ├── boundaries.py   Paris Open Data – arrondissement polygons
│       ├── revenus.py      INSEE – local income (stub)
│       ├── air_quality.py  Airparif – air quality (stub)
│       └── crime.py        SSMSI – crime statistics (stub)
├── logs/                   Per-source .log files
├── main.py                 Pipeline entry point
└── requirements.txt
```

---

## Quick start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Run the full pipeline

```bash
python main.py
```

### 3. Run specific sources

```bash
# DVF + OSM only
python main.py --sources dvf osm

# DVF with a custom date window
python main.py --sources dvf --date-min 2023-01-01 --date-max 2023-12-31

# Boundaries (one-time reference data)
python main.py --sources boundaries
```

### 4. Dry run (no HTTP calls)

```bash
python main.py --dry-run
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

## Completing the stubs

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
