FROM python:3.12.15-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70 AS builder

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 VIRTUAL_ENV=/opt/venv
RUN python -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
WORKDIR /build
# Run-time dependencies only; test and analysis tools stay out of the image.
COPY services/api/requirements-runtime.lock.txt ./requirements.lock.txt
RUN pip install --no-cache-dir --require-hashes -r requirements.lock.txt \
    && pip uninstall --yes pip

FROM python:3.12.15-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70 AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 VIRTUAL_ENV=/opt/venv PATH="/opt/venv/bin:$PATH"
# Debian security updates published since the base image was built, then the PDF libraries.
# Nothing installs packages at run time, so pip, setuptools and their bundled wheels go (they
# carry their own vendored copies of urllib3, certifi and others).
RUN apt-get update && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
    fonts-inter fonts-noto-core libharfbuzz-subset0 libpango-1.0-0 libpangoft2-1.0-0 poppler-utils \
    && rm -rf /var/lib/apt/lists/* \
    && rm -rf /usr/local/bin/pip* /usr/local/lib/python3.12/site-packages/pip* \
       /usr/local/lib/python3.12/site-packages/setuptools* /usr/local/lib/python3.12/ensurepip \
    && groupadd --system app && useradd --system --gid app --create-home app
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY services/api/app ./app
COPY services/api/alembic.ini ./alembic.ini
COPY services/api/migrations ./migrations
COPY services/api/gunicorn.conf.py ./gunicorn.conf.py
# The release (the VERSION file), passed by the release workflow and setup-hospital.sh.
ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION
USER app
EXPOSE 8000
CMD ["gunicorn", "--config", "/app/gunicorn.conf.py", "app.main:app"]
