# ═══════════════════════════════════════════════════════════════════════════════
# Urban Data Explorer — Makefile
# Architecture Medallion : Bronze → Silver → Gold → PostgreSQL → API + Frontend
#
# Commandes principales :
#   make all       →  Pipeline COMPLET puis lance les serveurs  (1ère mise en route)
#   make pipeline  →  Bronze complet + Silver + Gold + PostgreSQL
#   make refresh   →  Silver + Gold + PostgreSQL  (Bronze déjà téléchargé)
#   make dev       →  API (8000) + Frontend (5173)
#   make velib     →  Daemon micro-batch Vélib' (tourne en continu)
#   make status    →  Vérifie l'état de la stack
# ═══════════════════════════════════════════════════════════════════════════════

.DEFAULT_GOAL := help
SHELL         := /bin/bash
ROOT          := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

PG_CONTAINER  := urbandata-pg
PG_DB         := urbandata
PG_USER       := postgres
PG_PASS       := postgres
PG_PORT       := 5432

# ─────────────────────────────────────────────────────────────────────────────
# Aide
# ─────────────────────────────────────────────────────────────────────────────
.PHONY: help
help:
	@echo ""
	@echo "  Urban Data Explorer"
	@echo "  ═══════════════════════════════════════════════════"
	@echo ""
	@echo "  DÉMARRAGE RAPIDE"
	@echo "    make all          Pipeline complet + serveurs (1ère fois)"
	@echo "    make dev          Lance API + Frontend (données déjà en base)"
	@echo ""
	@echo "  PIPELINE DE DONNÉES"
	@echo "    make pipeline     Bronze (DL) + Silver + Gold + PostgreSQL"
	@echo "    make refresh      Silver + Gold + PostgreSQL (Bronze déjà là)"
	@echo "    make bronze       Télécharge toutes les sources Bronze"
	@echo "    make silver-gold  Calcule Silver + Gold (sans PG export)"
	@echo "    make pg-export    Exporte Gold → PostgreSQL"
	@echo ""
	@echo "  SERVEURS"
	@echo "    make dev          API (8000) + Frontend (5173) en parallèle"
	@echo "    make api          FastAPI seul"
	@echo "    make frontend     React dev server seul"
	@echo "    make velib        Daemon micro-batch Vélib' (30s)"
	@echo ""
	@echo "  INFRA"
	@echo "    make pg           Démarre PostgreSQL Docker"
	@echo "    make pg-stop      Arrête PostgreSQL Docker"
	@echo "    make pg-reset     Supprime les tables Gold (pour repartir)"
	@echo "    make install      Installe les dépendances Python + npm"
	@echo "    make status       Vérifie l'état de la stack"
	@echo ""

# ─────────────────────────────────────────────────────────────────────────────
# PostgreSQL Docker
# ─────────────────────────────────────────────────────────────────────────────
.PHONY: pg
pg:
	@docker start $(PG_CONTAINER) 2>/dev/null \
		|| docker run -d \
			--name $(PG_CONTAINER) \
			-e POSTGRES_DB=$(PG_DB) \
			-e POSTGRES_USER=$(PG_USER) \
			-e POSTGRES_PASSWORD=$(PG_PASS) \
			-p $(PG_PORT):5432 \
			postgres:15-alpine
	@echo "  PostgreSQL → localhost:$(PG_PORT) / db=$(PG_DB)"
	@sleep 2

.PHONY: pg-stop
pg-stop:
	@docker stop $(PG_CONTAINER) 2>/dev/null && echo "  PostgreSQL arrêté" || echo "  Déjà arrêté"

.PHONY: pg-reset
pg-reset: pg
	@echo "  Suppression des tables Gold..."
	@docker exec $(PG_CONTAINER) psql -U $(PG_USER) -d $(PG_DB) -c \
		"DROP TABLE IF EXISTS gold_arrondissement_summary, gold_indicator_scores, gold_poi_catalog, gold_price_timeline CASCADE;" \
		2>/dev/null && echo "  Tables supprimées. Relancez 'make refresh'." || true

# ─────────────────────────────────────────────────────────────────────────────
# Installation des dépendances
# ─────────────────────────────────────────────────────────────────────────────
.PHONY: install
install:
	pip install -r requirements.txt
	cd frontend && npm install

# ─────────────────────────────────────────────────────────────────────────────
# Bronze — téléchargement des sources historiques (DVF, OSM, boundaries…)
# ─────────────────────────────────────────────────────────────────────────────
.PHONY: bronze
bronze:
	@echo ">>> BRONZE — sources historiques (DVF, OSM, boundaries, crime, revenus…)"
	@echo "    Durée estimée : 5-15 min selon la connexion internet"
	python3 main.py

# ─────────────────────────────────────────────────────────────────────────────
# Silver + Gold (calculs analytiques, sans téléchargement)
# ─────────────────────────────────────────────────────────────────────────────
.PHONY: silver-gold
silver-gold:
	@echo ">>> SILVER + GOLD — calcul des scores et tables analytiques"
	python3 pipeline.py --skip-bronze --skip-pg

# ─────────────────────────────────────────────────────────────────────────────
# Export PostgreSQL uniquement (Gold → PG)
# ─────────────────────────────────────────────────────────────────────────────
.PHONY: pg-export
pg-export: pg
	@echo ">>> EXPORT → PostgreSQL"
	python3 -m src.gold.export_pg

# ─────────────────────────────────────────────────────────────────────────────
# refresh : Bronze indicateurs + Silver + Gold + PG
# (télécharge connectivity/health_env/tranquility, sans re-télécharger DVF/OSM)
# ─────────────────────────────────────────────────────────────────────────────
.PHONY: refresh
refresh: pg
	@echo ">>> REFRESH (Bronze indicateurs + Silver + Gold + PostgreSQL)"
	python3 pipeline.py --mobility-once

# ─────────────────────────────────────────────────────────────────────────────
# pipeline : Bronze COMPLET + Silver + Gold + PG
# ─────────────────────────────────────────────────────────────────────────────
.PHONY: pipeline
pipeline: pg bronze
	@echo ">>> Silver + Gold + PostgreSQL"
	python3 pipeline.py --skip-bronze --mobility-once

# ─────────────────────────────────────────────────────────────────────────────
# Serveurs de développement
# ─────────────────────────────────────────────────────────────────────────────
.PHONY: dev
dev: pg
	@bash scripts/start_dev.sh

.PHONY: api
api:
	uvicorn api.main:app --reload --port 8000

.PHONY: frontend
frontend:
	cd frontend && npm run dev

# ─────────────────────────────────────────────────────────────────────────────
# Daemon Vélib' micro-batch (tourne en continu toutes les 60s)
# ─────────────────────────────────────────────────────────────────────────────
.PHONY: velib
velib:
	@echo ">>> Daemon Vélib' micro-batch (Ctrl+C pour arrêter)"
	python3 -m src.ingestion.mobility_micro_batch

# ─────────────────────────────────────────────────────────────────────────────
# all : pipeline complet + démarrage des serveurs
# ─────────────────────────────────────────────────────────────────────────────
.PHONY: all
all: pipeline dev

# ─────────────────────────────────────────────────────────────────────────────
# Status : état de la stack
# ─────────────────────────────────────────────────────────────────────────────
.PHONY: status
status:
	@echo ""
	@echo "  === PostgreSQL Docker ==="
	@docker inspect -f '  État : {{.State.Status}}' $(PG_CONTAINER) 2>/dev/null || echo "  Conteneur inexistant"
	@echo ""
	@echo "  === Tables Gold dans PostgreSQL ==="
	@docker exec $(PG_CONTAINER) psql -U $(PG_USER) -d $(PG_DB) -c \
		"SELECT tablename, (SELECT COUNT(*) FROM information_schema.columns WHERE table_name = tablename) AS colonnes FROM pg_tables WHERE tablename LIKE 'gold_%' ORDER BY tablename;" \
		2>/dev/null || echo "  PostgreSQL non joignable"
	@echo ""
	@echo "  === Fichiers Gold (Parquet) ==="
	@ls -lh data/gold/*.parquet 2>/dev/null || echo "  (aucun fichier)"
	@echo ""
	@echo "  === API FastAPI ==="
	@curl -s http://localhost:8000/health 2>/dev/null | python3 -m json.tool || echo "  API hors ligne"
	@echo ""
