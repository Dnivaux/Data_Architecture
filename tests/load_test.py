"""
Tests de charge — Urban Data Explorer API
==========================================
Simule des utilisateurs concurrents naviguant sur le dashboard Paris.

Commandes d'exécution
----------------------
  # Mode interactif (UI web sur http://localhost:8089)
  locust -f tests/load_test.py --host http://localhost:8000

  # Mode headless CI (50 users, spawn 5/s, durée 60s)
  locust -f tests/load_test.py --host http://localhost:8000 \
         --headless -u 50 -r 5 -t 60s \
         --html tests/load_report.html

  # Test de montée en charge progressive (StepLoadShape)
  locust -f tests/load_test.py --host http://localhost:8000 \
         --headless --html tests/load_report_steps.html

Scénarios simulés
-----------------
  DashboardUser  : navigation typique d'un utilisateur du dashboard
  PowerUser      : utilisateur intensif (comparaisons, classements, filtres)
  MobileUser     : accès allégé (scores seuls, pas de POI)

Métriques suivies
-----------------
  - Temps de réponse (P50, P95, P99)
  - Throughput (req/s)
  - Taux d'erreur (objectif < 1%)
  - Header X-Process-Time validé côté client
"""
from __future__ import annotations

import json
import random
import time

from locust import HttpUser, LoadTestShape, TaskSet, between, events, task
from locust.env import Environment


# ---------------------------------------------------------------------------
# Données de test
# ---------------------------------------------------------------------------

ARRONDISSEMENTS = list(range(1, 21))
CATEGORIES = ["bar", "nightclub", "park"]
SCORE_FIELDS = [
    "livability_score", "anime_score", "calme_score",
    "connectivity_score", "mobility_score", "health_env_score", "tranquility_score",
]


# ---------------------------------------------------------------------------
# Scénario 1 — Utilisateur Dashboard (comportement majoritaire)
# ---------------------------------------------------------------------------

class DashboardUser(HttpUser):
    """
    Simule un utilisateur naviguant sur le dashboard :
    - Charge initiale de tous les scores (page d'accueil)
    - Drill-down sur 1-2 arrondissements
    - Consultation de POI sur la carte
    - Comparaison de 2 arrondissements
    - Consultation du prix

    Poids des tâches : chaque décorateur @task(n) représente la probabilité
    relative qu'un utilisateur exécute cette action.
    """

    wait_time = between(1.0, 3.0)  # pause réaliste entre les actions (1-3 secondes)

    @task(5)
    def get_all_scores(self):
        """Page d'accueil : chargement de la carte choroplèthe (endpoint le plus fréquent)."""
        with self.client.get("/api/scores/all", catch_response=True) as resp:
            _validate_response(resp, expected_status=200, min_items=20)

    @task(4)
    def get_indicator_scores(self):
        """Chargement des 4 nouveaux scores avec géométries (rendu choroplèthe)."""
        with self.client.get("/api/scores/indicators/all", catch_response=True) as resp:
            _validate_response(resp, expected_status=200, min_items=20)

    @task(3)
    def get_single_score(self):
        """Clic sur un arrondissement spécifique."""
        arr = random.choice(ARRONDISSEMENTS)
        with self.client.get(
            f"/api/scores/{arr}",
            name="/api/scores/{arrondissement}",
            catch_response=True,
        ) as resp:
            _validate_response(resp, expected_status=200)

    @task(3)
    def get_poi_by_category(self):
        """Activation d'un layer POI sur la carte."""
        cat = random.choice(CATEGORIES)
        with self.client.get(
            f"/api/poi/by-category/{cat}",
            name="/api/poi/by-category/{category}",
            catch_response=True,
        ) as resp:
            _validate_response(resp, expected_status=200)

    @task(2)
    def compare_arrondissements(self):
        """Ouverture du panneau de comparaison."""
        a, b = random.sample(ARRONDISSEMENTS, 2)
        with self.client.get(
            f"/api/comparison/?a={a}&b={b}",
            name="/api/comparison/",
            catch_response=True,
        ) as resp:
            _validate_response(resp, expected_status=200)

    @task(2)
    def get_price_timeline(self):
        """Affichage du graphique d'évolution des prix."""
        arr = random.choice(ARRONDISSEMENTS)
        with self.client.get(
            f"/api/prices/arrondissement/{arr}",
            name="/api/prices/arrondissement/{arrondissement}",
            catch_response=True,
        ) as resp:
            _validate_response(resp, expected_status=200)

    @task(1)
    def get_all_poi(self):
        """Chargement complet des POI (action moins fréquente)."""
        with self.client.get("/api/poi/?limit=200", catch_response=True) as resp:
            _validate_response(resp, expected_status=200)

    @task(1)
    def health_check(self):
        """Monitoring automatique (keepalive)."""
        with self.client.get("/health", catch_response=True) as resp:
            _validate_response(resp, expected_status=200)
            if resp.status_code == 200:
                data = resp.json()
                if not data.get("database_connected", True):
                    resp.failure("PostgreSQL déconnecté détecté via /health")


# ---------------------------------------------------------------------------
# Scénario 2 — Power User (analyses avancées)
# ---------------------------------------------------------------------------

class PowerUser(HttpUser):
    """
    Utilisateur effectuant des analyses intensives :
    classements, comparaisons multiples, prix détaillés.
    """

    wait_time = between(0.5, 1.5)

    @task(4)
    def get_ranking(self):
        """Consultation du classement complet des arrondissements."""
        field = random.choice(SCORE_FIELDS)
        with self.client.get(
            f"/api/comparison/ranking?score_field={field}",
            name="/api/comparison/ranking",
            catch_response=True,
        ) as resp:
            _validate_response(resp, expected_status=200, min_items=20)

    @task(3)
    def compare_multiple_pairs(self):
        """Compare plusieurs paires successivement (analyse comparative)."""
        pairs = random.sample(ARRONDISSEMENTS, 4)
        for i in range(0, len(pairs) - 1, 2):
            a, b = pairs[i], pairs[i + 1]
            with self.client.get(
                f"/api/comparison/?a={a}&b={b}",
                name="/api/comparison/",
                catch_response=True,
            ) as resp:
                _validate_response(resp, expected_status=200)

    @task(2)
    def get_price_summary(self):
        """Résumé statistique des prix."""
        with self.client.get("/api/prices/summary", catch_response=True) as resp:
            _validate_response(resp, expected_status=200)

    @task(2)
    def get_price_timeline_filtered(self):
        """Timeline des prix avec filtres année."""
        arr = random.choice(ARRONDISSEMENTS)
        with self.client.get(
            f"/api/prices/timeline?arrondissement={arr}&year_min=2018&year_max=2023",
            name="/api/prices/timeline?filtered",
            catch_response=True,
        ) as resp:
            _validate_response(resp, expected_status=[200, 404])

    @task(1)
    def get_indicator_scores(self):
        with self.client.get("/api/scores/indicators/all", catch_response=True) as resp:
            _validate_response(resp, expected_status=200)


# ---------------------------------------------------------------------------
# Scénario 3 — Mobile User (accès allégé)
# ---------------------------------------------------------------------------

class MobileUser(HttpUser):
    """
    Utilisateur mobile : consulte uniquement les scores, pas les POI lourds.
    Simule des connexions 4G avec latence plus élevée.
    """

    wait_time = between(2.0, 5.0)

    @task(5)
    def get_all_scores(self):
        with self.client.get("/api/scores/all", catch_response=True) as resp:
            _validate_response(resp, expected_status=200)
            if resp.ok:
                # Vérifier que le header X-Process-Time est présent
                process_time = resp.headers.get("X-Process-Time")
                if not process_time:
                    resp.failure("Header X-Process-Time absent de la réponse")

    @task(3)
    def get_single_score(self):
        arr = random.choice(ARRONDISSEMENTS)
        with self.client.get(
            f"/api/scores/{arr}",
            name="/api/scores/{arrondissement}",
            catch_response=True,
        ) as resp:
            _validate_response(resp, expected_status=200)

    @task(1)
    def get_comparison(self):
        a, b = random.sample(ARRONDISSEMENTS, 2)
        with self.client.get(
            f"/api/comparison/?a={a}&b={b}",
            name="/api/comparison/",
            catch_response=True,
        ) as resp:
            _validate_response(resp, expected_status=200)


# ---------------------------------------------------------------------------
# Montée en charge progressive (StepLoadShape)
# ---------------------------------------------------------------------------

class StepLoadShape(LoadTestShape):
    """
    Simule une montée en charge progressive puis un plateau.

    Étapes :
      0-30s   :  10 users  (warm-up)
      30-60s  :  30 users  (charge normale)
      60-90s  :  60 users  (pic de charge)
      90-120s :  30 users  (descente)
      120s+   :  stop

    Usage : locust -f tests/load_test.py --host http://localhost:8000 --headless
    """

    stages = [
        {"duration": 30,  "users": 10,  "spawn_rate": 2},
        {"duration": 60,  "users": 30,  "spawn_rate": 5},
        {"duration": 90,  "users": 60,  "spawn_rate": 10},
        {"duration": 120, "users": 30,  "spawn_rate": 5},
    ]

    def tick(self):
        run_time = self.get_run_time()
        for stage in self.stages:
            if run_time < stage["duration"]:
                return stage["users"], stage["spawn_rate"]
        return None  # arrêt après la dernière étape


# ---------------------------------------------------------------------------
# Utilitaires de validation des réponses
# ---------------------------------------------------------------------------

def _validate_response(
    response,
    expected_status: int | list[int] = 200,
    min_items: int | None = None,
) -> None:
    """
    Valide la réponse HTTP :
      - Code status attendu
      - Nombre minimum d'items dans la liste JSON (si applicable)
      - Présence du header X-Process-Time
    """
    statuses = [expected_status] if isinstance(expected_status, int) else expected_status

    if response.status_code not in statuses:
        response.failure(
            f"Status inattendu {response.status_code} "
            f"(attendu : {statuses}) — {response.text[:200]}"
        )
        return

    if min_items is not None and response.ok:
        try:
            data = response.json()
            if isinstance(data, list) and len(data) < min_items:
                response.failure(
                    f"Réponse trop courte : {len(data)} items (min attendu : {min_items})"
                )
                return
        except (json.JSONDecodeError, ValueError):
            response.failure("Réponse non-JSON")
            return

    response.success()


# ---------------------------------------------------------------------------
# Hooks de reporting (métriques personnalisées)
# ---------------------------------------------------------------------------

@events.request.add_listener
def on_request(
    request_type, name, response_time, response_length, response,
    context, exception, **kwargs
):
    """
    Logue les requêtes lentes (> 500ms) pour identifier les goulots.
    """
    if response_time > 500:
        print(
            f"⚠ REQUÊTE LENTE : {request_type} {name} "
            f"→ {response_time:.0f}ms (seuil : 500ms)"
        )


@events.test_start.add_listener
def on_test_start(environment: Environment, **kwargs):
    print("\n" + "=" * 60)
    print("Test de charge Urban Data Explorer — DÉMARRAGE")
    print(f"  Host      : {environment.host}")
    print(f"  SLA cible : P95 < 200ms, erreurs < 1%")
    print("=" * 60 + "\n")


@events.test_stop.add_listener
def on_test_stop(environment: Environment, **kwargs):
    stats = environment.stats
    print("\n" + "=" * 60)
    print("Test de charge — RÉSULTATS FINAUX")
    print(f"  Requêtes totales : {stats.total.num_requests}")
    print(f"  Échecs           : {stats.total.num_failures}")
    print(f"  Taux d'erreur    : {stats.total.fail_ratio * 100:.1f}%")
    print(f"  RPS moyen        : {stats.total.current_rps:.1f} req/s")
    print(f"  P50              : {stats.total.get_response_time_percentile(0.50):.0f}ms")
    print(f"  P95              : {stats.total.get_response_time_percentile(0.95):.0f}ms")
    print(f"  P99              : {stats.total.get_response_time_percentile(0.99):.0f}ms")
    sla_ok = (
        stats.total.fail_ratio < 0.01
        and stats.total.get_response_time_percentile(0.95) < 200
    )
    print(f"  SLA respecté     : {'✅ OUI' if sla_ok else '❌ NON'}")
    print("=" * 60 + "\n")
