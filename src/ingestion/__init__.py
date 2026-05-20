from .dvf import ingest as ingest_dvf
from .osm import ingest as ingest_osm
from .revenus import ingest as ingest_revenus
from .air_quality import ingest as ingest_air_quality
from .crime import ingest as ingest_crime
from .boundaries import ingest as ingest_boundaries

# Nouveaux indicateurs stratégiques
from .connectivity import ingest as ingest_connectivity
from .health_environment import ingest as ingest_health_environment
from .tranquility import ingest as ingest_tranquility
from .mobility_micro_batch import run_once as ingest_mobility_batch

__all__ = [
    # Sources historiques
    "ingest_dvf",
    "ingest_osm",
    "ingest_revenus",
    "ingest_air_quality",
    "ingest_crime",
    "ingest_boundaries",
    # Nouveaux indicateurs (Bronze statique)
    "ingest_connectivity",
    "ingest_health_environment",
    "ingest_tranquility",
    # Mobilité micro-batch (un seul batch à la demande)
    "ingest_mobility_batch",
]
