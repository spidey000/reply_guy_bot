# =============================================================================
# Reply Guy Bot - Dockerfile
# =============================================================================
# Build: docker build -t reply-guy-bot .
# Run:   docker run --env-file .env reply-guy-bot

FROM python:3.11-slim-bookworm AS builder
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# =============================================================================
# Runtime image
# =============================================================================
FROM python:3.11-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PATH=/opt/venv/bin:$PATH

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY src/ ./src/
COPY config/ ./config/
COPY scripts/ ./scripts/
COPY VERSION .

RUN mkdir -p /app/logs /app/data \
    && ln -sf /app/logs/x_session_audit.log /app/x_session_audit.log \
    && useradd --create-home appuser \
    && chown -R appuser:appuser /app
USER appuser

HEALTHCHECK --interval=60s --timeout=30s --start-period=120s --retries=3 CMD [ "python", "scripts/healthcheck.py" ]

CMD ["python", "-m", "src.bot"]
