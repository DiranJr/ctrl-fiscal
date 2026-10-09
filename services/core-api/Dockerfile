# Contexto de build: services/   (docker compose define context=./services)
FROM python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt \
    && useradd --uid 10001 --system --no-create-home --shell /usr/sbin/nologin ctrl
COPY shared/ctrl_common /app/ctrl_common
COPY core-api/core_api /app/core_api
COPY core-api/migrations /app/migrations
COPY core-api/alembic.ini /app/alembic.ini
USER 10001
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready', timeout=3)"
CMD ["uvicorn", "core_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header", "--no-access-log"]
