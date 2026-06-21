"""
Tests de charge Locust — Urban Data Explorer API
=================================================
Répond au critère RNCP C1.1 : « Des tests de charge sont réalisés confirmant
l'intégrité et la performance de la base de données » (et de l'API qui l'expose).

Mesure latence et débit des endpoints Gold servis depuis PostgreSQL, sous
charge concurrente, pour valider le dimensionnement du pool de connexions
(api/dependencies.py : pool_size=10, max_overflow=20).

Pré-requis
----------
  pip install locust
  # L'API doit tourner : python -m api.main  (port 8000)

Lancement
---------
  # Interface web (http://localhost:8089)
  locust -f tests/locustfile.py --host http://localhost:8000

  # Mode headless : 100 utilisateurs, montée de 10/s, 1 minute
  locust -f tests/locustfile.py --host http://localhost:8000 \
         --headless -u 100 -r 10 -t 1m --csv=loadtest

Objectif de performance (SLA)
-----------------------------
  P95 < 200 ms sur /api/scores/all et /api/scores/indicators/all
  0 % d'erreurs HTTP 5xx sous 100 utilisateurs concurrents
"""
from __future__ import annotations

import os
import random

from locust import HttpUser, between, task

# Clé d'API : passée si l'auth est activée côté serveur (API_KEYS défini).
_API_KEY = os.environ.get("LOCUST_API_KEY", "dev-key-123")
_HEADERS = {"X-API-Key": _API_KEY} if _API_KEY else {}


class DashboardUser(HttpUser):
    """Simule un utilisateur du dashboard qui consulte scores, prix et POI."""

    wait_time = between(0.5, 2.5)  # réflexion utilisateur entre deux actions

    @task(5)
    def all_scores(self):
        self.client.get("/api/scores/all", headers=_HEADERS, name="/api/scores/all")

    @task(4)
    def indicator_scores(self):
        self.client.get(
            "/api/scores/indicators/all", headers=_HEADERS,
            name="/api/scores/indicators/all",
        )

    @task(3)
    def one_arrondissement(self):
        n = random.randint(1, 20)
        self.client.get(f"/api/scores/{n}", headers=_HEADERS, name="/api/scores/[n]")

    @task(2)
    def price_timeline(self):
        n = random.randint(1, 20)
        self.client.get(
            "/api/prices/timeline", params={"arrondissement": n},
            headers=_HEADERS, name="/api/prices/timeline",
        )

    @task(2)
    def social_housing_timeline(self):
        n = random.randint(1, 20)
        self.client.get(
            "/api/social-housing/timeline", params={"arrondissement": n},
            headers=_HEADERS, name="/api/social-housing/timeline",
        )

    @task(1)
    def poi(self):
        self.client.get("/api/poi/", params={"limit": 200}, headers=_HEADERS, name="/api/poi/")

    @task(1)
    def health(self):
        self.client.get("/health", name="/health")
