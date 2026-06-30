# Data Science Marketing — image commune API + dashboard.
# Copiée dans le clone du repo par deploy.sh sous le nom Dockerfile.pi.
FROM python:3.11-slim

WORKDIR /app

# libgomp1 : requis par xgboost ; curl : healthchecks.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Ports : 8000 (FastAPI) et 8501 (Streamlit). La commande est définie par le compose.
EXPOSE 8000 8501
