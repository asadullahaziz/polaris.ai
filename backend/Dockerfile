# Polaris AI backend — one image: DRF + Channels + polaris_agent + Inngest handler.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# System libraries:
#   * GDAL / GEOS / PROJ  -> GeoDjango (baked into the image, not installed at runtime)
#   * curl for debugging (health probe uses python, so curl is optional)
RUN apt-get update && apt-get install -y --no-install-recommends \
        binutils \
        gdal-bin \
        libgdal-dev \
        libgeos-dev \
        libproj-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first for layer caching. INSTALL_DEV=true pulls test/lint tools.
COPY requirements.txt requirements-dev.txt ./
ARG INSTALL_DEV=false
RUN pip install --upgrade pip && \
    if [ "$INSTALL_DEV" = "true" ]; then \
        pip install -r requirements-dev.txt; \
    else \
        pip install -r requirements.txt; \
    fi

COPY . .
RUN chmod +x /app/entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
# Async ASGI server; --lifespan on runs the checkpointer-pool startup hook.
CMD ["uvicorn", "config.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--lifespan", "on"]
