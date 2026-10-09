# Contexto de build: services/   — o worker reutiliza o domínio fiscal (ctrl_fiscal).
FROM python:3.13-slim-bookworm@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt \
    && useradd --uid 10001 --system --no-create-home --shell /usr/sbin/nologin ctrl \
    && mkdir -p /data/exports && chown 10001 /data/exports && chmod 700 /data/exports
COPY shared/ctrl_common /app/ctrl_common
COPY fiscal-api/ctrl_fiscal /app/ctrl_fiscal
COPY fiscal-worker/fiscal_worker /app/fiscal_worker
USER 10001
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=4 \
  CMD python -c "import os,time,sys; p='/tmp/worker-heartbeat'; sys.exit(0 if os.path.exists(p) and time.time()-os.path.getmtime(p) < 60 else 1)"
CMD ["python", "-m", "fiscal_worker.worker"]
