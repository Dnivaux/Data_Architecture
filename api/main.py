"""
Urban Data Explorer – FastAPI Backend
======================================
REST API servant les données Gold depuis PostgreSQL.

Endpoints :
  GET /api/scores/all              – Scores vivabilité (20 arrondissements)
  GET /api/scores/{n}              – Score d'un arrondissement
  GET /api/scores/indicators/all   – 4 nouveaux scores stratégiques
  GET /api/poi/                    – POI (filtrage par catégorie optionnel)
  GET /api/poi/by-category/{cat}   – POI par catégorie
  GET /api/prices/timeline         – Série temporelle DVF
  GET /api/prices/arrondissement/{n} – Historique prix d'un arrondissement
  GET /api/comparison/             – Comparaison de 2 arrondissements
  GET /health                      – Health check + métriques pool DB
  GET /docs                        – Swagger UI

Middlewares :
  X-Process-Time : temps de traitement en ms ajouté à chaque réponse
  CORS           : origins configurables via ALLOWED_ORIGINS (env)
"""
from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from api.dependencies import get_db_status, verify_db_connection
from api.routers import (
    chantiers, comparison, connectivity, housing, iris, live, mobility, poi, prices, scores, social_housing,
)
from api.schemas import HealthCheck, HealthCheckExtended
from api.security import APP_ENV, enforce_startup_security, install_rate_limiting, require_api_key

# ---------------------------------------------------------------------------
# Logging structuré (format : timestamp | level | logger | message)
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("api")

# ---------------------------------------------------------------------------
# Lifespan — startup/shutdown PostgreSQL-aware
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup : garde-fou sécurité + connexion PostgreSQL. Shutdown : libère le pool."""
    logger.info("=" * 60)
    logger.info("Urban Data Explorer API — démarrage (env=%s)", APP_ENV)
    logger.info("=" * 60)

    # Fail-closed : en production, refuse de démarrer sans clés d'API.
    enforce_startup_security()

    db_ok = verify_db_connection()
    if not db_ok:
        logger.warning(
            "PostgreSQL introuvable au démarrage. "
            "Configurer DATABASE_URL dans .env. "
            "L'API démarre quand même mais les endpoints retourneront 503."
        )
    else:
        status = get_db_status()
        logger.info(
            "Pool PostgreSQL prêt — size=%d, overflow=%d, host=%s",
            status["pool_size"], status["overflow"], status["database_url"],
        )

    yield  # ← l'application tourne ici

    # Shutdown : dispose du pool proprement
    from api.dependencies import engine
    engine.dispose()
    logger.info("Pool PostgreSQL fermé — arrêt propre")


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

# En production, on masque la documentation interactive et le schéma OpenAPI
# (réduction de la surface d'exposition / fingerprinting). Activable via ENABLE_DOCS=1.
_DOCS_ENABLED = APP_ENV != "production" or os.environ.get("ENABLE_DOCS") == "1"

app = FastAPI(
    title="Urban Data Explorer API",
    description=(
        "API géospatiale pour l'analyse logement & qualité de vie à Paris. "
        "Données servies depuis PostgreSQL (tables Gold du pipeline Medallion)."
    ),
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs" if _DOCS_ENABLED else None,
    redoc_url="/redoc" if _DOCS_ENABLED else None,
    openapi_url="/openapi.json" if _DOCS_ENABLED else None,
)

# Quotas (rate limiting) — no-op si slowapi non installé
install_rate_limiting(app)

# ---------------------------------------------------------------------------
# Middleware 1 — Mesure du temps de traitement (Critère C2.4)
# ---------------------------------------------------------------------------


@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    """
    Mesure le temps exact de traitement de chaque requête.

    Ajoute dans les headers de réponse :
      X-Process-Time : durée en millisecondes (ex: "4.27ms")

    Logue également : méthode, path, status_code, durée.
    Permet de confirmer les critères de performance (SLA < 200ms en P99).
    """
    start_ns = time.perf_counter_ns()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter_ns() - start_ns) / 1_000_000  # ns → ms

    response.headers["X-Process-Time"] = f"{elapsed_ms:.2f}ms"

    # Log structuré — lisible par ELK, Datadog, ou grep simple
    logger.info(
        "%s %s → %d | %.2fms",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response


# ---------------------------------------------------------------------------
# Middleware 2 — En-têtes de sécurité (defense-in-depth, OWASP Secure Headers)
# ---------------------------------------------------------------------------


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Ajoute les en-têtes de sécurité recommandés à chaque réponse."""

    # Routes de documentation : Swagger UI / ReDoc chargent des assets depuis un
    # CDN puis font un fetch de /openapi.json. Une CSP "default-src 'none'" les
    # bloque → page blanche. On y applique donc une CSP permissive ciblée.
    _DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        if request.url.path in self._DOCS_PATHS:
            # CSP permissive pour la doc interactive (CDN jsdelivr + init inline + fetch self).
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
                "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
                "img-src 'self' https://fastapi.tiangolo.com data:; "
                "worker-src blob:; "
                "connect-src 'self'"
            )
        else:
            # API JSON : aucune ressource active n'est servie → CSP verrouillée.
            response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        # HSTS uniquement en production (suppose une terminaison TLS en amont).
        if APP_ENV == "production":
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response


app.add_middleware(SecurityHeadersMiddleware)

# ---------------------------------------------------------------------------
# Middleware 3 — Validation du Host header (anti Host-header injection)
# ---------------------------------------------------------------------------

_ALLOWED_HOSTS = [
    h.strip() for h in os.environ.get("ALLOWED_HOSTS", "*").split(",") if h.strip()
]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_ALLOWED_HOSTS)

# ---------------------------------------------------------------------------
# Middleware 4 — CORS (allowlist stricte, sans wildcard)
# ---------------------------------------------------------------------------

# Allowlist explicite d'origines. PAS de "*" : combiné à allow_credentials,
# le wildcard revient à autoriser n'importe quelle origine (faille).
_ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get(
        "ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:8080,http://localhost:5173",
    ).split(",") if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,          # allowlist stricte (aucun wildcard)
    allow_credentials=False,                 # l'API s'authentifie par en-tête X-API-Key, pas par cookie
    allow_methods=["GET", "OPTIONS"],        # API en lecture seule
    allow_headers=["X-API-Key", "Content-Type"],
    expose_headers=["X-Process-Time", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
    max_age=600,
)

# ---------------------------------------------------------------------------
# Routeurs
# ---------------------------------------------------------------------------

# Toutes les routes /api exigent une clé d'API valide (X-API-Key) si API_KEYS
# est défini ; sinon l'auth est désactivée (mode dev). Voir api/security.py.
_auth = [Depends(require_api_key)]

app.include_router(scores.router,       prefix="/api", dependencies=_auth)
app.include_router(iris.router,         prefix="/api", dependencies=_auth)
app.include_router(poi.router,          prefix="/api", dependencies=_auth)
app.include_router(prices.router,       prefix="/api", dependencies=_auth)
app.include_router(comparison.router,   prefix="/api", dependencies=_auth)
app.include_router(mobility.router,     prefix="/api", dependencies=_auth)
app.include_router(chantiers.router,    prefix="/api", dependencies=_auth)
app.include_router(connectivity.router, prefix="/api", dependencies=_auth)
app.include_router(social_housing.router, prefix="/api", dependencies=_auth)
app.include_router(housing.router,      prefix="/api", dependencies=_auth)
# Routeur temps réel (WebSocket) : auth gérée en interne (query param pour le WS)
app.include_router(live.router, prefix="/api")

# ---------------------------------------------------------------------------
# Endpoints système
# ---------------------------------------------------------------------------


@app.get(
    "/health",
    response_model=HealthCheckExtended,
    tags=["system"],
    summary="Health check avec métriques pool DB",
)
async def health_check():
    """
    Vérifie l'état de l'API et de la connexion PostgreSQL.
    Retourne les métriques du pool de connexions pour le monitoring.
    """
    from api.dependencies import engine
    from sqlalchemy import text

    db_ok = False
    gold_tables: list[str] = []
    try:
        with engine.connect() as conn:
            db_ok = True
            rows = conn.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name LIKE 'gold_%' ORDER BY table_name"
            )).fetchall()
            gold_tables = [r[0] for r in rows]
    except Exception as exc:
        logger.error("Health check DB error: %s", exc)

    pool_status = get_db_status()

    return HealthCheckExtended(
        status="ok" if db_ok else "degraded",
        message="API opérationnelle" if db_ok else "PostgreSQL inaccessible",
        database_connected=db_ok,
        gold_tables_found=gold_tables,
        pool_size=pool_status["pool_size"],
        pool_checked_out=pool_status["checked_out"],
        database_host=pool_status["database_url"],
    )


@app.get("/", tags=["system"], include_in_schema=False)
async def root():
    return JSONResponse(status_code=307, headers={"Location": "/docs"})


# ---------------------------------------------------------------------------
# Point d'entrée uvicorn
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        # Ne surveiller QUE le code API : évite que le reloader thrash sur
        # data/, logs/, frontend/node_modules (écritures pipeline / npm install).
        reload_dirs=["api"],
        log_level="info",
        access_log=False,  # désactivé : notre middleware gère le logging
    )
