#!/usr/bin/env bash
# ============================================================
# Déploiement des 3 stacks Docker sur le Raspberry Pi.
# À lancer SUR LE PI, depuis le clone du repo Urban :
#     bash ~/apps/urban/deploy/pi/deploy.sh
#
# Prérequis : Docker + plugin compose installés, et ~/apps/.env rempli.
# N'installe rien dans OpenResty (voir wire-openresty.sh pour ça).
# ============================================================
set -euo pipefail

APPS="$HOME/apps"
ENV_FILE="$APPS/.env"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # …/urban/deploy/pi
URBAN_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"                  # racine du repo urban

MARKETING_REPO="https://github.com/Dnivaux/Data_Science_Marketing.git"
AISCA_REPO="https://github.com/Dnivaux/aisca_medical.git"

DC() { docker compose --env-file "$ENV_FILE" "$@"; }

# ---------- garde-fous ----------
command -v docker >/dev/null || { echo "ERREUR : Docker non installé. Voir README §2."; exit 1; }
mkdir -p "$APPS"
if [[ ! -f "$ENV_FILE" ]]; then
  cp "$SCRIPT_DIR/.env.example" "$ENV_FILE"
  echo "ERREUR : $ENV_FILE créé depuis l'exemple. Remplis-le (clés, mots de passe) puis relance."
  exit 1
fi

# ---------- clone / maj des repos ----------
clone_or_pull() { # $1=url  $2=dir
  if [[ -d "$2/.git" ]]; then git -C "$2" pull --ff-only; else git clone "$1" "$2"; fi
}
echo "==> Récupération des repos…"
clone_or_pull "$MARKETING_REPO" "$APPS/marketing"
clone_or_pull "$AISCA_REPO"     "$APPS/aisca"
# urban : on utilise le clone courant ($URBAN_DIR), supposé déjà à jour.

# ---------- URBAN ----------
echo "==> Build & up URBAN…"
( cd "$URBAN_DIR" && DC -f docker-compose.pi.yml up -d --build )

# ---------- MARKETING ----------
echo "==> Préparation MARKETING…"
cp "$SCRIPT_DIR/marketing/Dockerfile"            "$APPS/marketing/Dockerfile.pi"
cp "$SCRIPT_DIR/marketing/docker-compose.pi.yml" "$APPS/marketing/docker-compose.pi.yml"
# Patch : utils.py doit lire API_URL depuis l'environnement (pour joindre le service api).
UTILS="$APPS/marketing/app_dashboard/utils.py"
if ! grep -q 'os.getenv("API_URL"' "$UTILS"; then
  grep -q '^import os' "$UTILS" || sed -i '1i import os' "$UTILS"
  sed -i 's#^API_URL = "http://localhost:8000"#API_URL = os.getenv("API_URL", "http://localhost:8000")#' "$UTILS"
fi
echo "==> Build & up MARKETING…"
( cd "$APPS/marketing" && DC -f docker-compose.pi.yml up -d --build )

# ---------- AISCA ----------
echo "==> Préparation AISCA…"
cp "$SCRIPT_DIR/aisca/Dockerfile.api"          "$APPS/aisca/Dockerfile.pi-api"
cp "$SCRIPT_DIR/aisca/docker-compose.pi.yml"   "$APPS/aisca/docker-compose.pi.yml"
cp "$SCRIPT_DIR/aisca/Dockerfile.frontend"     "$APPS/aisca/frontend/Dockerfile.pi-front"
cp "$SCRIPT_DIR/aisca/nginx.conf"              "$APPS/aisca/frontend/nginx.conf"
echo "==> Build & up AISCA (le build SBERT/torch peut prendre ~10-20 min la 1re fois)…"
( cd "$APPS/aisca" && DC -f docker-compose.pi.yml up -d --build )

echo
echo "==> Conteneurs actifs :"
docker ps --format 'table {{.Names}}\t{{.Ports}}\t{{.Status}}' | grep -Ei 'urban|marketing|aisca' || true
echo
echo "OK. Étapes suivantes : (1) bash wire-openresty.sh  (vhosts+TLS+auth)"
echo "                       (2) depuis le PC : bash deploy/pi/sync-urban-data.sh  (données Urban)"
