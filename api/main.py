"""
Urban Data Explorer – FastAPI Backend
======================================
REST API serving enriched geospatial data from the Silver/Gold layers to MapLibre frontend.

Endpoints:
  /api/scores/*        – Livability scores (Animé, Calme, Accessibilité financière)
  /api/poi/*           – Points of Interest (bars, nightclubs, parks)
  /api/prices/*        – Property price timeline (2014-2023)
  /api/comparison/*    – Compare two arrondissements
  /health              – Health check
  /docs                – Swagger UI
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.dependencies import get_data_cache
from api.routers import comparison, poi, prices, scores
from api.schemas import HealthCheck

# ============================================================================
# Lifespan
# ============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    # Startup: preload common tables into cache
    cache = get_data_cache()
    try:
        from api.dependencies import load_gold_table
        load_gold_table("arrondissement_summary.parquet")
        load_gold_table("poi_catalog.parquet")
        load_gold_table("price_timeline.parquet")
    except Exception as e:
        print(f"Warning: Failed to preload cache: {e}")

    yield

    # Shutdown: clear cache
    cache.clear()


# ============================================================================
# App instantiation
# ============================================================================

app = FastAPI(
    title="Urban Data Explorer API",
    description="Geospatial API for Paris housing & lifestyle analysis",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS middleware for frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:8080", "*"],  # TODO: restrict in prod
    allow_credentials=True,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["*"],
)

# ============================================================================
# Routes
# ============================================================================

app.include_router(scores.router, prefix="/api")
app.include_router(poi.router, prefix="/api")
app.include_router(prices.router, prefix="/api")
app.include_router(comparison.router, prefix="/api")


@app.get("/health", response_model=HealthCheck, tags=["system"])
async def health_check():
    """Health check endpoint."""
    return HealthCheck(
        status="ok",
        message="Urban Data Explorer API is running",
    )


@app.get("/", tags=["system"], include_in_schema=False)
async def root():
    """Root redirect to docs."""
    return JSONResponse(
        status_code=307,
        headers={"Location": "/docs"},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
