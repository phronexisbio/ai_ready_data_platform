# postgres:18 (no -slim variant published for major version 18 at build time):
# guarantees pg_dump's version matches the catalog's Postgres server version
# (18.4) — pg_dump should be >= the server it dumps.
FROM postgres:18

RUN apt-get update && apt-get install -y --no-install-recommends wget ca-certificates && \
    wget -q -O /usr/local/bin/mc https://dl.min.io/client/mc/release/linux-amd64/mc && \
    chmod +x /usr/local/bin/mc && \
    rm -rf /var/lib/apt/lists/*

COPY infra/backup/backup.sh /usr/local/bin/backup.sh
RUN chmod +x /usr/local/bin/backup.sh

ENTRYPOINT ["/usr/local/bin/backup.sh"]
