# Frontend Urban Data Explorer — build Vite puis service statique via nginx.
# Utilisé pour le déploiement Pi (urban.daenivaux.fr) derrière OpenResty.
# Les appels API sont relatifs (/api, /health) → proxifiés par OpenResty vers urban-api.

FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
# npm install (et non ci) pour que rollup résolve son binaire natif linux-arm64 sur le Pi
RUN npm install --no-audit --no-fund
COPY . .
# Valeurs injectées au build (par docker-compose.pi.yml). Vides = URLs relatives.
ARG VITE_API_URL=""
ARG VITE_API_KEY=""
# On écrase le .env committé (dev-key-123) pour garantir les valeurs de prod.
RUN printf "VITE_API_URL=%s\nVITE_API_KEY=%s\n" "$VITE_API_URL" "$VITE_API_KEY" > .env
RUN npm run build

FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
