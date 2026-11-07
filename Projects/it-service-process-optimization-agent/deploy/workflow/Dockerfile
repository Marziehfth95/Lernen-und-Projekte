ARG PYTHON_BASE_IMAGE=python:3.12.16-slim-bookworm@sha256:623674a14ef93d052bb9b0de4dbf5f28eb578a35263838941cc8aa43213c1487
FROM ${PYTHON_BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/home/app \
    PORT=8000

WORKDIR /app
RUN addgroup --system --gid 10001 app \
    && adduser --system --uid 10001 --ingroup app --home /home/app app
COPY deploy/pip-bootstrap.lock /tmp/pip-bootstrap.lock
RUN python -m pip install --require-hashes -r /tmp/pip-bootstrap.lock \
    && rm /tmp/pip-bootstrap.lock
COPY deploy/workflow/requirements.lock /tmp/requirements.lock
# pip is build-only: removing it also removes its vendored urllib3, msgpack and pkg_resources.
RUN python -m pip install --require-hashes -r /tmp/requirements.lock \
    && rm /tmp/requirements.lock \
    && python -m pip uninstall --yes pip
COPY src ./src
COPY deploy/aggregate-package/generated/ /mnt/aggregates/
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8000\")}/health', timeout=3)"]
CMD ["python", "-m", "uvicorn", "process_optimizer.main:app", "--host", "0.0.0.0", "--port", "8000"]
