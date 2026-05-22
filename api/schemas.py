"""
Pydantic v2 — Schémas de validation requête/réponse de l'API.
"""
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Scores de vivabilité
# ---------------------------------------------------------------------------

class ArrondissementScore(BaseModel):
    """Scores de vivabilité complets pour un arrondissement (historiques + nouveaux)."""

    arrondissement: int = Field(..., ge=1, le=20)

    # --- Scores historiques ---
    anime_score:         float = Field(0.0, ge=0, le=100, description="Score d'animation (bars, clubs, parcs)")
    calme_score:         float = Field(0.0, ge=0, le=100, description="Score de calme (criminalité inversée + air)")
    accessibilite_score: float = Field(0.0, ge=0, le=100, description="Score d'accessibilité financière")

    # --- Nouveaux scores stratégiques ---
    connectivity_score:  Optional[float] = Field(None, ge=0, le=100, description="Score connectivité fibre + 4G/5G + T2/T3")
    mobility_score:      Optional[float] = Field(None, ge=0, le=100, description="Score mobilité Vélib' + PRIM")
    health_env_score:    Optional[float] = Field(None, ge=0, le=100, description="Score santé environnementale (végétalisation + îlots)")
    tranquility_score:   Optional[float] = Field(None, ge=0, le=100, description="Score tranquillité (inverse bruit + crime + nightlife)")

    # --- Score composite global ---
    livability_score:    Optional[float] = Field(None, ge=0, le=100, description="Score composite pondéré (0-100)")

    # --- Métriques brutes ---
    bar_count:           int            = Field(0, description="Nombre de bars (OSM)")
    nightclub_count:     int            = Field(0, description="Nombre de boîtes de nuit (OSM)")
    park_count:          int            = Field(0, description="Nombre de parcs (OSM)")
    median_price:            Optional[float] = Field(None, description="Prix médian DVF (€/m²)")
    social_housing_pct:      Optional[float] = Field(None, description="% logements sociaux (déprécié)")
    nombre_logements_sociaux: Optional[int]  = Field(None, description="Nb logements sociaux (stock total)")


class ArrondissementDetail(BaseModel):
    """
    Vue détaillée d'un arrondissement : scores + métriques brutes + géométrie WKT.
    Utilisée pour le rendu choroplèthe sur carte (MapLibre).
    """

    arrondissement:      int            = Field(..., ge=1, le=20)
    nom_arrondissement:  str            = Field(..., description="Ex: 'Paris 14e'")
    geometry_wkt:        Optional[str]  = Field(None, description="Polygone WKT EPSG:4326")

    # Scores
    anime_score:         Optional[float] = None
    calme_score:         Optional[float] = None
    accessibilite_score: Optional[float] = None
    connectivity_score:  Optional[float] = None
    mobility_score:      Optional[float] = None
    health_env_score:    Optional[float] = None
    tranquility_score:   Optional[float] = None
    livability_score:    Optional[float] = None

    # Métriques clés connectivité
    pct_eligible_ftth:   Optional[float] = Field(None, description="% locaux éligibles fibre")
    pct_pop_4g_mean:     Optional[float] = Field(None, description="% population couverte 4G")
    pct_t2_t3:           Optional[float] = Field(None, description="% logements T2/T3")

    # Métriques clés mobilité
    station_count_velib: Optional[int]   = Field(None, description="Nb stations Vélib'")
    avg_bikes_available: Optional[float] = Field(None, description="Disponibilité moy. vélos")

    # Métriques clés santé environnementale
    nb_ilots_fraicheur:  Optional[int]   = Field(None, description="Nb îlots de fraîcheur")
    surface_fraicheur_ha:Optional[float] = Field(None, description="Surface espaces verts (ha)")
    arbres_per_km2:      Optional[float] = Field(None, description="Densité arborée")

    # Métriques clés tranquillité
    crime_count_total:   Optional[int]   = Field(None, description="Délits enregistrés")
    crime_rate_per_1000: Optional[float] = Field(None, description="Taux délinquance/1000 hab")
    noise_lden_surface_ha: Optional[float] = Field(None, description="Surface exposée Lden ≥55dB (ha)")
    nb_bars:             Optional[int]   = None
    nb_nightclubs:       Optional[int]   = None

    # Prix
    median_price:        Optional[float] = Field(None, description="Prix médian DVF (€)")

    # Logement social
    nombre_logements_sociaux: Optional[int] = Field(None, description="Nb logements sociaux (dernière année)")


# ---------------------------------------------------------------------------
# POI
# ---------------------------------------------------------------------------

class POI(BaseModel):
    """Point d'intérêt (bar, parc, boîte de nuit)."""

    id:                   int            = Field(..., description="OSM element id")
    type:                 str            = Field(..., description="node ou way")
    category:             str            = Field(..., description="bar | nightclub | park")
    name:                 Optional[str]  = None
    lat:                  float          = Field(..., ge=-90,  le=90)
    lon:                  float          = Field(..., ge=-180, le=180)
    hours:                Optional[str]  = Field(None, description="Opening hours OSM")
    wheelchair_accessible:Optional[str]  = None


# ---------------------------------------------------------------------------
# Prix DVF
# ---------------------------------------------------------------------------

class PriceTimeline(BaseModel):
    """Prix médian DVF pour un arrondissement × année."""

    arrondissement:    int            = Field(..., ge=1, le=20)
    year:              int            = Field(..., ge=2014, le=2030)
    median_price:      Optional[float] = None
    transaction_count: int            = Field(0)


# ---------------------------------------------------------------------------
# Comparaison
# ---------------------------------------------------------------------------

class ArrondissementComparison(BaseModel):
    """Comparaison côte-à-côte de deux arrondissements."""

    arrond_a:       int                    = Field(..., ge=1, le=20)
    arrond_b:       int                    = Field(..., ge=1, le=20)
    scores_a:       ArrondissementScore
    scores_b:       ArrondissementScore
    price_diff:     Optional[float]        = Field(None, description="Prix B - Prix A (€)")
    livability_diff:Optional[float]        = Field(None, description="Vivabilité B - Vivabilité A")


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class HealthCheck(BaseModel):
    """Réponse minimale du health check."""
    status:  str
    message: str


class HealthCheckExtended(HealthCheck):
    """Health check étendu avec métriques PostgreSQL."""
    database_connected:  bool       = False
    gold_tables_found:   list[str]  = Field(default_factory=list)
    pool_size:           int        = 0
    pool_checked_out:    int        = 0
    database_host:       str        = ""
