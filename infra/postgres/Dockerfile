# PostgreSQL with pgBackRest for point-in-time recovery (Phase 7, task 7.7.2; ADR 011).
# The same image runs the database (which archives every WAL segment as it completes) and the
# backup service (scheduled full and differential backups into the same repository).
FROM postgres:16.15-bookworm@sha256:e8a07d2c7579b038938cdba6c539e580e5001caf9ea203d397b5cc83e76bd938

# Debian security updates since the base image was built, then pgBackRest from the PostgreSQL
# project's own repository (already configured in this image). The upstream gosu binary is
# removed (built with an old Go standard library that image scans flag); gosu.sh replaces it.
RUN apt-get update && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends pgbackrest=2.59.3-1.pgdg12+1 \
    && rm -rf /var/lib/apt/lists/* \
    && install -d -o postgres -g postgres -m 0750 /var/lib/pgbackrest /var/spool/pgbackrest \
    && install -d -o postgres -g postgres -m 0700 /var/lib/postgresql/restore \
    && install -d -o postgres -g postgres -m 0755 /var/lib/osa-backup \
    && install -d -m 0755 /etc/pgbackrest \
    && rm -f /usr/local/bin/gosu

COPY infra/postgres/pgbackrest.conf /etc/pgbackrest/pgbackrest.conf
COPY --chmod=0755 infra/postgres/with-backup-secrets.sh /usr/local/bin/with-backup-secrets
COPY --chmod=0755 infra/postgres/backup-loop.sh /usr/local/bin/backup-loop
COPY --chmod=0755 infra/postgres/gosu.sh /usr/sbin/gosu

# The database and the backup service run as PostgreSQL's own user, never root: the volumes
# take their owner from the directories above when created.
USER postgres
