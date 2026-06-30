#!/usr/bin/env bash
# ============================================================
# Synchronise les données Gold d'Urban (Postgres + Mongo) du PC LOCAL vers le Pi.
# À lancer depuis la RACINE du repo Urban, sur le PC de dev (Git Bash), avec la
# stack locale démarrée (docker compose up -d postgres mongo).
#
# Usage :  bash deploy/pi/sync-urban-data.sh [user@host]
#          (défaut : daenivaux@192.168.1.111)
# ============================================================
set -euo pipefail

PI="${1:-daenivaux@192.168.1.111}"
REMOTE="cd ~/apps/urban && docker compose --env-file ~/apps/.env -f docker-compose.pi.yml"

echo "==> Vérification des conteneurs locaux (postgres, mongo)…"
docker compose ps postgres mongo >/dev/null

echo "==> Dump + restore PostgreSQL (urbandata) vers le Pi…"
docker compose exec -T postgres pg_dump -U postgres -Fc urbandata \
  | ssh "$PI" "$REMOTE exec -u postgres -T postgres pg_restore -U postgres -d urbandata --clean --if-exists --no-owner"

echo "==> Dump + restore MongoDB (urbandata) vers le Pi…"
docker compose exec -T mongo mongodump --db urbandata --archive \
  | ssh "$PI" "$REMOTE exec -T mongo mongorestore --drop --archive"

echo "==> Terminé. Vérifie :  curl -u USER:PASS https://urban.daenivaux.fr/api/scores/all"
