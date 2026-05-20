"""
API Dependencies — Connexion PostgreSQL via SQLAlchemy
======================================================
Remplace entièrement l'ancienne architecture Parquet + DataCache.

Architecture
------------
  engine        : connexion globale avec pool (pool_size=5, max_overflow=10)
  SessionLocal  : factory de sessions SQLAlchemy
  get_db()      : générateur FastAPI — injecte et ferme proprement une session par requête

Configuration
-------------
  DATABASE_URL (env) : URL SQLAlchemy complète
  Défaut             : postgresql://postgres:postgres@localhost:5432/urbandata

Avantages vs Parquet cache
--------------------------
  - Données toujours à jour (pas de TTL à gérer)
  - Connexion universellement accessible (réseau, API, tooling SQL)
  - Gestion des transactions et des erreurs par SQLAlchemy
  - Connection pooling natif pour absorber la charge concurrente (tests Locust)
"""
from __future__ import annotations

import logging
import os
from typing import Generator

from dotenv import load_dotenv
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

load_dotenv()

logger = logging.getLogger("api.db")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATABASE_URL: str = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/urbandata",
)

# ---------------------------------------------------------------------------
# Engine avec connection pooling (dimensionné pour tests de charge Locust)
# ---------------------------------------------------------------------------

engine: Engine = create_engine(
    DATABASE_URL,
    pool_size=10,           # connexions persistantes dans le pool
    max_overflow=20,        # connexions supplémentaires tolérées en pic
    pool_pre_ping=True,     # vérifie les connexions stale avant usage
    pool_recycle=300,       # recycle les connexions toutes les 5 min (évite timeout PG)
    echo=False,             # passer à True pour logger le SQL en debug
    connect_args={
        "connect_timeout": 5,     # échec rapide si PG injoignable
        "application_name": "urban_data_api",
    },
)

# Log chaque nouvelle connexion ouverte dans le pool (utile pour le monitoring)
@event.listens_for(engine, "connect")
def _on_connect(dbapi_connection, connection_record):
    logger.debug("Nouvelle connexion ouverte dans le pool PostgreSQL")


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------

SessionLocal: sessionmaker = sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,   # évite des SELECT supplémentaires post-commit
)

# ---------------------------------------------------------------------------
# Dépendance FastAPI — injectée dans chaque endpoint via Depends(get_db)
# ---------------------------------------------------------------------------

def get_db() -> Generator[Session, None, None]:
    """
    Générateur de session SQLAlchemy.

    Garantit la fermeture de la session même en cas d'exception,
    libérant la connexion dans le pool pour les requêtes concurrentes.

    Usage dans un routeur :
        @router.get("/")
        def my_endpoint(db: Session = Depends(get_db)):
            rows = db.execute(text("SELECT ...")).mappings().all()
            ...
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Utilitaires de santé et de validation
# ---------------------------------------------------------------------------

def verify_db_connection() -> bool:
    """
    Vérifie que PostgreSQL est joignable et que les tables Gold existent.
    Utilisé dans le lifespan de l'application pour le health check au démarrage.
    """
    try:
        with engine.connect() as conn:
            result = conn.execute(text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_name LIKE 'gold_%'"
            ))
            count = result.scalar()
            logger.info(
                "PostgreSQL connecté — %d table(s) Gold trouvée(s)", count or 0
            )
            return True
    except Exception as exc:
        logger.error("Connexion PostgreSQL échouée : %s", exc)
        return False


def get_db_status() -> dict:
    """Retourne les métriques du pool de connexions (pour /health étendu)."""
    pool = engine.pool
    return {
        "pool_size":      pool.size(),
        "checked_in":     pool.checkedin(),
        "checked_out":    pool.checkedout(),
        "overflow":       pool.overflow(),
        "invalid":        pool.invalid(),
        "database_url":   DATABASE_URL.split("@")[-1],  # masque les credentials
    }
