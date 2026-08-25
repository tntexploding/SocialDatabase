FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SOCIAL_DATABASE_DB_PATH=/data/members.db \
    SOCIAL_DATABASE_HOST=0.0.0.0 \
    SOCIAL_DATABASE_PORT=8000 \
    SOCIAL_DATABASE_DOCS=false

WORKDIR /opt/social-database

RUN addgroup --system socialdb \
    && adduser --system --ingroup socialdb socialdb \
    && mkdir -p /data \
    && chown socialdb:socialdb /data

COPY pyproject.toml README.md LICENSE MANIFEST.in ./
COPY social_database ./social_database

RUN python -m pip install --no-cache-dir ".[server]"

USER socialdb
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/ready', timeout=3).read()"]

CMD ["social-database", "serve"]
