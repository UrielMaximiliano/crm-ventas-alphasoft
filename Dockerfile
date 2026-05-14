FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEBIAN_FRONTEND=noninteractive

# Dependencias del sistema:
# - libpq5: para psycopg2
# - curl, ca-certificates: healthchecks y descargas
# - tini: reapa zombies (importante cuando Scrapling abre subprocesos de browser)
# - Chromium runtime libs: necesarias para Patchright/Scrapling en modo Google Maps
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
        tini \
        libpq5 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libatspi2.0-0 \
        libcups2 \
        libdrm2 \
        libgbm1 \
        libgtk-3-0 \
        libdbus-glib-1-2 \
        libxt6 \
        libasound2 \
        libxkbcommon0 \
        libxtst6 \
        libnspr4 \
        libnss3 \
        libx11-xcb1 \
        libxrandr2 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxshmfence1 \
        libcairo2 \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libfontconfig1 \
        fonts-liberation \
        xvfb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Cachear instalacion de deps Python
COPY pyproject.toml ./
COPY README.md* ./

RUN pip install --upgrade pip setuptools wheel \
    && pip install -e .

# Descargar el browser de patchright (Chromium con anti-detect). ~300MB.
# Si falla (red, build offline), se reintenta en runtime cuando se llama al scraper.
RUN patchright install --with-deps chromium 2>/dev/null \
    || playwright install --with-deps chromium 2>/dev/null \
    || echo "WARN: install de browser fallo - se reintenta en runtime"

COPY app/ ./app/
COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY data/ ./data/

EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
