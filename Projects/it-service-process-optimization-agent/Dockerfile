# Compatibility wrapper for the workflow image. Prefer deploy/workflow/Dockerfile.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app
RUN addgroup --system app && adduser --system --ingroup app app

COPY pyproject.toml README.md ./
COPY src ./src
COPY deploy/aggregate-package /mnt/aggregates

RUN pip install --no-cache-dir '.[cloud,observability]'

USER app
EXPOSE 8000

CMD ["sh", "-c", "uvicorn process_optimizer.main:app --host 0.0.0.0 --port ${PORT}"]
