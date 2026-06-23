"""
Sécurité de l'API — Authentification par clé + quotas (rate limiting)
=====================================================================
Répond au critère RNCP C2.1 : « Les mécanismes d'authentification sont
déployés » et « Les autorisations et les quotas imposés aux utilisateurs
de l'API sont faciles à comprendre et à intégrer ».

1) Authentification par clé d'API
---------------------------------
  • En-tête HTTP : `X-API-Key: <clé>`
  • Clés valides : variable d'environnement `API_KEYS` (liste séparée par des virgules)
  • Si `API_KEYS` est vide → authentification DÉSACTIVÉE (mode développement),
    un avertissement est journalisé. En production, définir `API_KEYS`.
  • Les endpoints publics (/health, /docs, /) restent ouverts.

2) Quotas (rate limiting)
-------------------------
  • Implémenté avec `slowapi` (token-bucket par clé d'API, sinon par IP).
  • Limite par défaut : `RATE_LIMIT` (défaut « 120/minute »).
  • Dépassement → HTTP 429 + en-tête `Retry-After`.
  • Si slowapi n'est pas installé, le rate limiting est ignoré proprement
    (l'API démarre quand même) — installer via `pip install slowapi`.
"""
from __future__ import annotations

import hmac
import logging
import os

from dotenv import load_dotenv
from fastapi import Header, HTTPException, Request, status

load_dotenv()
logger = logging.getLogger("api.security")

# Environnement d'exécution : "development" (défaut) ou "production".
# En production, l'absence de clés d'API fait échouer le démarrage (fail-closed).
APP_ENV = os.environ.get("APP_ENV", "development").strip().lower()

# ---------------------------------------------------------------------------
# 1) Authentification par clé d'API
# ---------------------------------------------------------------------------

def _load_api_keys() -> set[str]:
    raw = os.environ.get("API_KEYS", "").strip()
    return {k.strip() for k in raw.split(",") if k.strip()}


API_KEYS: set[str] = _load_api_keys()

if not API_KEYS:
    logger.warning(
        "API_KEYS non défini — authentification DÉSACTIVÉE (mode dev). "
        "Définir API_KEYS=clé1,clé2 dans .env pour activer l'auth."
    )


def is_valid_api_key(candidate: str | None) -> bool:
    """Vérifie une clé d'API en temps constant (anti timing-attack).

    Compare la clé candidate à chaque clé valide via `hmac.compare_digest`,
    SANS court-circuit (`any(...)` early-exit), pour ne pas révéler par le
    temps de réponse combien de caractères correspondent.
    """
    if not candidate:
        return False
    valid = False
    for key in API_KEYS:
        if hmac.compare_digest(candidate, key):
            valid = True  # pas de `return` → temps constant
    return valid


def enforce_startup_security() -> None:
    """Garde-fou « fail-closed » à appeler au démarrage de l'application.

    En production (`APP_ENV=production`), refuse de démarrer si aucune clé
    d'API n'est configurée : on ne veut JAMAIS d'API ouverte en prod par oubli.
    """
    if APP_ENV == "production" and not API_KEYS:
        raise RuntimeError(
            "APP_ENV=production mais API_KEYS est vide — démarrage refusé "
            "(fail-closed). Définir API_KEYS=clé1,clé2 pour activer l'auth."
        )


async def require_api_key(x_api_key: str | None = Header(default=None)) -> str:
    """
    Dépendance FastAPI : vérifie l'en-tête `X-API-Key`.

    - Auth désactivée (aucune clé configurée) → laisse passer (`"anonymous"`).
    - Clé manquante / invalide → HTTP 401 (message générique, sans distinguer
      « manquante » de « invalide » pour ne pas faciliter l'énumération).
    """
    if not API_KEYS:
        return "anonymous"

    if not is_valid_api_key(x_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Clé d'API manquante ou invalide. Fournissez l'en-tête 'X-API-Key'.",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    return x_api_key


# ---------------------------------------------------------------------------
# 2) Quotas — rate limiting (slowapi, importé de façon défensive)
# ---------------------------------------------------------------------------

RATE_LIMIT = os.environ.get("RATE_LIMIT", "120/minute")


def _rate_limit_key(request: Request) -> str:
    """Clé de comptage : la clé d'API si fournie, sinon l'IP cliente."""
    api_key = request.headers.get("x-api-key")
    if api_key:
        return f"key:{api_key}"
    client = request.client.host if request.client else "unknown"
    return f"ip:{client}"


try:
    from slowapi import Limiter
    from slowapi.errors import RateLimitExceeded
    from slowapi.middleware import SlowAPIMiddleware
    from slowapi.util import get_remote_address  # noqa: F401  (réexport pratique)

    limiter: "Limiter | None" = Limiter(
        key_func=_rate_limit_key,
        default_limits=[RATE_LIMIT],
        headers_enabled=True,  # ajoute X-RateLimit-* aux réponses
    )
    SLOWAPI_AVAILABLE = True
    logger.info("Rate limiting actif (slowapi) — limite par défaut : %s", RATE_LIMIT)
except Exception:  # noqa: BLE001
    limiter = None
    RateLimitExceeded = None       # type: ignore[assignment]
    SlowAPIMiddleware = None       # type: ignore[assignment]
    SLOWAPI_AVAILABLE = False
    logger.warning(
        "slowapi indisponible — quotas désactivés. "
        "Installer avec `pip install slowapi` pour activer le rate limiting."
    )


def install_rate_limiting(app) -> None:
    """Branche le limiter sur l'application FastAPI (no-op si slowapi absent)."""
    if not SLOWAPI_AVAILABLE or limiter is None:
        return
    from slowapi import _rate_limit_exceeded_handler

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
