# Déploiement des 3 projets sur le Raspberry Pi 5 (`*.daenivaux.fr`)

Héberge **Urban Data Explorer**, **Data Science Marketing** et **AISCA Medical** sur le Pi
(192.168.1.111), derrière l'**OpenResty existant** (qui sert déjà `daenivaux.fr` — non modifié),
avec un portail à 3 vignettes sur **`projet.daenivaux.fr`** et une **Basic Auth** devant tout.

```
projet.daenivaux.fr      → portail (3 vignettes)
urban.daenivaux.fr       → Urban Data Explorer   (front + API + Airflow à la demande)
marketing.daenivaux.fr   → dashboard Streamlit Marketing
aisca.daenivaux.fr       → AISCA Medical (front React + API SBERT/Gemini)
```

Tous les conteneurs écoutent sur `127.0.0.1` ; seul OpenResty (80/443) est exposé.

---

## Plan des ports sur le Pi
| Service | Port | | Service | Port |
|---|---|---|---|---|
| Next.js (existant) | 3000 *(ne pas toucher)* | | marketing-api | 8002 |
| *(existant)* | 8080 *(ne pas toucher)* | | marketing-streamlit | 8012 |
| urban-api | 8001 | | aisca-api | 8003 |
| urban-frontend | 8011 | | aisca-frontend | 8013 |
| urban-airflow (profile) | 8081 | | | |

---

## 0. Accès SSH sans mot de passe (une seule fois, depuis le PC)

Dans **Git Bash** :
```bash
ls ~/.ssh/id_ed25519.pub || ssh-keygen -t ed25519        # crée une clé si absente
ssh-copy-id daenivaux@192.168.1.111                       # tape ton mot de passe Pi une fois
ssh daenivaux@192.168.1.111 'echo OK && docker --version' # doit répondre sans mot de passe
```
*(PowerShell, si `ssh-copy-id` absent :)*
```powershell
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh daenivaux@192.168.1.111 "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

## 1. DNS (chez ton registrar / Cloudflare)
Ajoute **un enregistrement wildcard** pointant vers l'IP **publique** du Pi :
```
*.daenivaux.fr   A   <IP_PUBLIQUE_DU_PI>     (+ proxy DÉSACTIVÉ si Cloudflare, "DNS only")
```
Vérifie : `nslookup urban.daenivaux.fr` doit renvoyer l'IP du Pi.
*(Box : rediriger les ports 80 et 443 vers 192.168.1.111 si accès depuis Internet.)*

## 2. Docker sur le Pi (si absent)
```bash
ssh daenivaux@192.168.1.111
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && exit         # reconnecte-toi pour appliquer le groupe
```

## 3. Cloner Urban + remplir le `.env`
```bash
ssh daenivaux@192.168.1.111
mkdir -p ~/apps && git clone https://github.com/Dnivaux/Data_Architecture.git ~/apps/urban
cp ~/apps/urban/deploy/pi/.env.example ~/apps/.env
nano ~/apps/.env          # URBAN_API_KEYS, POSTGRES_PASSWORD, GOOGLE_API_KEY, BASIC_AUTH_*
```

## 4. Déployer les 3 stacks Docker
```bash
bash ~/apps/urban/deploy/pi/deploy.sh
```
Clone marketing + aisca, applique les patches, build & démarre tout (Airflow reste éteint).
Le **1er build d'aisca** (torch + SBERT) prend ~10-20 min.

## 5. TLS (certificat wildcard)
Réutilise l'outil déjà en place pour `daenivaux.fr`. Avec certbot (challenge DNS-01) :
```bash
sudo certbot certonly --manual --preferred-challenges dns \
  -d 'daenivaux.fr' -d '*.daenivaux.fr'
```
*(Sinon, par sous-domaine en HTTP-01 — nécessite que les vhosts répondent déjà en 80 :*
`sudo certbot --nginx -d projet.daenivaux.fr -d urban.daenivaux.fr -d marketing.daenivaux.fr -d aisca.daenivaux.fr`*)*
Les `.conf` pointent vers `/etc/letsencrypt/live/daenivaux.fr/` — adapte si besoin.

## 6. Câbler OpenResty (vhosts + Basic Auth + portail)
```bash
sudo -E bash ~/apps/urban/deploy/pi/wire-openresty.sh
```
Copie les 4 vhosts, génère le htpasswd, dépose le portail, **teste** (`nginx -t`) puis recharge.
> Sécurité : si le test échoue, **aucun reload** → `daenivaux.fr` reste intact.

## 7. Charger les données d'Urban (depuis le PC)
Stack locale Urban démarrée (`docker compose up -d postgres mongo`), puis dans Git Bash à la racine du repo :
```bash
bash deploy/pi/sync-urban-data.sh
```
Dump+restore de Postgres (Gold) et Mongo vers le Pi.

---

## Vérification
```bash
curl -u USER:PASS https://urban.daenivaux.fr/health           # {"status":"ok",...}
curl -u USER:PASS https://urban.daenivaux.fr/api/scores/all   # données présentes
```
Au navigateur (login Basic Auth) : `projet.daenivaux.fr` (3 vignettes) → chaque projet charge,
dashboards peuplés, aisca `/analyze` répond, et **`daenivaux.fr` toujours OK**.

## Airflow (démo à la demande)
```bash
cd ~/apps/urban
docker compose --env-file ~/apps/.env -f docker-compose.pi.yml --profile airflow up -d
# UI via tunnel SSH depuis le PC :  ssh -L 8081:127.0.0.1:8081 daenivaux@192.168.1.111
#   puis http://localhost:8081  (airflow / airflow)
docker compose --env-file ~/apps/.env -f docker-compose.pi.yml --profile airflow down  # arrêter
```

## Mise à jour d'un projet
```bash
git -C ~/apps/urban pull && bash ~/apps/urban/deploy/pi/deploy.sh   # rebuild ciblé
```

## Dépannage
- **502 Bad Gateway** : le conteneur n'écoute pas → `docker ps`, `docker logs <name>`.
- **`nginx -t` KO sur le cert** : le wildcard n'est pas émis (étape 5) ou mauvais chemin dans les `.conf`.
- **aisca 500** : `GOOGLE_API_KEY` manquante/invalide dans `~/apps/.env` → `docker logs aisca-api-1`.
- **Reboot** : tout repart seul (`restart: unless-stopped`) ; OpenResty est géré par systemd.
- **RAM** : `docker stats` ; n'allume Airflow que pour la démo.
