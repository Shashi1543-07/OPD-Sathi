FROM python:3.12.11-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 VIRTUAL_ENV=/opt/venv
RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
WORKDIR /build
COPY services/api/requirements.lock.txt ./requirements.lock.txt
RUN pip install --no-cache-dir --require-hashes -r requirements.lock.txt

FROM python:3.12.11-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 VIRTUAL_ENV=/opt/venv PATH="/opt/venv/bin:$PATH"
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-inter fonts-noto-core libharfbuzz-subset0 libpango-1.0-0 libpangoft2-1.0-0 poppler-utils \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app && useradd --system --gid app --create-home app
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY services/api/app ./app
COPY services/api/alembic.ini ./alembic.ini
COPY services/api/migrations ./migrations
USER app
EXPOSE 8000
CMD ["gunicorn", "app.main:app", "--worker-class", "uvicorn_worker.UvicornWorker", "--bind", "0.0.0.0:8000", "--workers", "2"]
