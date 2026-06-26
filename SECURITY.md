# Sécurité de l'API — Urban Data Explorer

Document de posture de sécurité de l'API FastAPI. Audit OWASP-orienté + mesures
de durcissement appliquées.

## Modèle de menace

API REST **en lecture seule** (GET) servant des données publiques agrégées
(scores de vivabilité, prix DVF, IRIS) depuis PostgreSQL. Surface :
- endpoints HTTP `/api/*` (auth par clé), `/health` (public), `/docs` (dev),
- un WebSocket temps réel `/api/live/velib`.

Pas de données personnelles, pas d'écriture côté client. Le risque principal est
l'abus (scraping/DoS), l'accès non autorisé en prod, et l'exfiltration d'infos
techniques via messages d'erreur.

## Mesures appliquées

| Domaine | Mesure | Où |
|---|---|---|
| **Authentification** | Clé d'API par en-tête `X-API-Key`. Comparaison **temps constant** (`hmac.compare_digest`, sans court-circuit) → anti timing-attack. | `api/security.py` |
| **Fail-closed prod** | `APP_ENV=production` sans `API_KEYS` → **refus de démarrer**. Jamais d'API ouverte par oubli. | `api/security.py`, `api/main.py` (lifespan) |
| **CORS** | Allowlist stricte d'origines, **sans wildcard**, `allow_credentials=False` (auth par en-tête, pas cookie), méthodes limitées à GET/OPTIONS. | `api/main.py` |
| **En-têtes de sécurité** | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy`, `Content-Security-Policy: default-src 'none'`, `HSTS` en prod. | `SecurityHeadersMiddleware` |
| **Host header** | `TrustedHostMiddleware` (allowlist `ALLOWED_HOSTS`) → anti Host-header injection / cache poisoning. | `api/main.py` |
| **Quotas (anti-DoS)** | `slowapi` token-bucket par clé d'API (sinon par IP), défaut `120/minute`, en-têtes `X-RateLimit-*`. | `api/security.py` |
| **Injection SQL** | Requêtes **paramétrées** (`:bind`). Les f-strings n'interpolent que des listes de colonnes **constantes** et un `score_field` validé par **allowlist**. | tous les routeurs |
| **Validation d'entrée** | Bornes Pydantic (`ge/le`) sur arrondissement (1-20), années ; `code_iris` restreint à `^\d{5,12}$`. | routeurs |
| **Fuite d'information** | Les exceptions DB ne sont **plus renvoyées** au client (message générique, log serveur). | `api/routers/comparison.py` |
| **Réduction de surface** | `/docs`, `/redoc`, `/openapi.json` **masqués en production** (sauf `ENABLE_DOCS=1`). | `api/main.py` |
| **WebSocket** | Auth via en-tête `X-API-Key` (préféré) ou query `?api_key=`, comparaison temps constant. | `api/routers/live.py` |
| **Secrets** | `.env` **non versionné** (`.gitignore`), `.env.example` fourni. Air quality via Open-Meteo (sans clé). | `.gitignore`, `.env.example` |

## Configuration recommandée en production

```bash
APP_ENV=production
API_KEYS=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
ALLOWED_ORIGINS=https://mon-front.exemple.fr
ALLOWED_HOSTS=api.exemple.fr
RATE_LIMIT=60/minute
```

- **TLS** : terminer le HTTPS en amont (reverse proxy / ingress). HSTS est alors émis.
- **PostgreSQL moindre privilège** : créer un rôle dédié à l'API avec `GRANT SELECT`
  sur les seules tables `gold_*`, distinct du rôle d'écriture du pipeline.
- **Rotation des clés** : `API_KEYS` accepte plusieurs clés (rotation sans coupure).

## Pistes d'amélioration (hors périmètre actuel)

- Journalisation/alerting des 401/429 répétés (détection d'abus).
- Clés d'API **hachées** au repos (stockage en base plutôt qu'en variable d'env).
- WAF / rate limiting distribué (Redis) si déploiement multi-instances.
