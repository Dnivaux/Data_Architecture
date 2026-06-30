#!/usr/bin/env bash
# ============================================================
# Câble les 4 vhosts (portail + 3 projets) dans l'OpenResty EXISTANT, sans
# toucher à daenivaux.fr. Sécurité : on ne recharge QUE si `nginx -t` réussit.
# À lancer SUR LE PI :   sudo -E bash ~/apps/urban/deploy/pi/wire-openresty.sh
#
# Variables (optionnelles) :
#   CONF_DIR   : dossier d'includes du http{} (auto-détecté sinon)
#   ENV_FILE   : défaut ~/apps/.env (pour BASIC_AUTH_USER / BASIC_AUTH_PASS)
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-$HOME/apps/.env}"
[[ -f "$ENV_FILE" ]] || ENV_FILE="/home/daenivaux/apps/.env"
# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a

# --- binaire nginx/openresty ---
NGINX_BIN="$(command -v openresty || command -v nginx || true)"
[[ -n "$NGINX_BIN" ]] || { echo "ERREUR : ni openresty ni nginx trouvé."; exit 1; }
echo "==> Binaire : $NGINX_BIN"

# --- dossier d'includes ---
if [[ -z "${CONF_DIR:-}" ]]; then
  for d in /etc/openresty/conf.d /usr/local/openresty/nginx/conf/conf.d \
           /etc/nginx/conf.d /etc/nginx/sites-enabled; do
    [[ -d "$d" ]] && { CONF_DIR="$d"; break; }
  done
fi
[[ -n "${CONF_DIR:-}" ]] || { echo "ERREUR : dossier d'includes introuvable. Relance avec CONF_DIR=…"; exit 1; }
echo "==> Vhosts copiés dans : $CONF_DIR"
echo "    (Vérifie que ton nginx.conf contient bien :  include $CONF_DIR/*.conf;)"

# --- htpasswd (Basic Auth) via openssl, sans apache2-utils ---
HTPASSWD="/etc/openresty/htpasswd_soutenance"
mkdir -p "$(dirname "$HTPASSWD")"
printf "%s:%s\n" "${BASIC_AUTH_USER:?}" "$(openssl passwd -apr1 "${BASIC_AUTH_PASS:?}")" > "$HTPASSWD"
chmod 640 "$HTPASSWD"
echo "==> htpasswd écrit : $HTPASSWD (user=$BASIC_AUTH_USER)"

# --- portail statique ---
mkdir -p /var/www/projet
cp "$SCRIPT_DIR/portal/index.html" /var/www/projet/index.html
echo "==> Portail déposé : /var/www/projet/index.html"

# --- certificat TLS ---
if [[ ! -f /etc/letsencrypt/live/daenivaux.fr/fullchain.pem ]]; then
  echo "ATTENTION : cert wildcard /etc/letsencrypt/live/daenivaux.fr/ absent."
  echo "  → émets-le (voir README §TLS) ou adapte ssl_certificate dans les .conf,"
  echo "    SINON 'nginx -t' échouera et rien ne sera rechargé (daenivaux.fr reste OK)."
fi

# --- copie des vhosts ---
cp "$SCRIPT_DIR"/openresty/*.conf "$CONF_DIR"/
echo "==> Vhosts copiés."

# --- test puis reload (reload UNIQUEMENT si le test passe) ---
echo "==> Test de configuration…"
if "$NGINX_BIN" -t; then
  "$NGINX_BIN" -s reload
  echo "==> OpenResty rechargé. daenivaux.fr intact, sous-domaines actifs."
else
  echo "ERREUR : 'nginx -t' a échoué → AUCUN reload effectué (config en service inchangée)."
  echo "         Corrige (cert TLS ?) puis relance."
  exit 1
fi
