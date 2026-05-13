from .dvf import ingest as ingest_dvf
from .osm import ingest as ingest_osm
from .revenus import ingest as ingest_revenus
from .air_quality import ingest as ingest_air_quality
from .crime import ingest as ingest_crime
from .boundaries import ingest as ingest_boundaries

__all__ = [
    "ingest_dvf",
    "ingest_osm",
    "ingest_revenus",
    "ingest_air_quality",
    "ingest_crime",
    "ingest_boundaries",
]
