# ============================================================
# Urban Data Explorer — image API (FastAPI + pipeline)
# ============================================================
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dépendances système minimales (psycopg2-binary n'exige pas de build, mais
# geopandas/shapely tirent libgeos ; curl sert au healthcheck).
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgeos-c1v5 curl \
    && rm -rf /var/lib/apt/lists/*

# Couche dépendances (cache Docker tant que requirements.txt ne change pas)
COPY requirements.txt .
RUN pip install -r requirements.txt

# Code applicatif
COPY . .

EXPOSE 8000

# Healthcheck → /health (résilience : le superviseur redémarre si dégradé)
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
