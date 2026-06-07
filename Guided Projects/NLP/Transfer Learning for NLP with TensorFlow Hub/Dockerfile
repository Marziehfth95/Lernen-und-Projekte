FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace/src \
    TF_CPP_MIN_LOG_LEVEL=2 \
    TFHUB_CACHE_DIR=/root/.cache/tfhub_modules

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    build-essential \
    curl \
    git \
    tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --upgrade pip && \
    python -m pip install -r /tmp/requirements.txt

COPY docker/start-workspace.sh /usr/local/bin/start-workspace
RUN chmod +x /usr/local/bin/start-workspace

EXPOSE 8888 6006

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["start-workspace"]
