#!/bin/sh
# Run from the repository root. This backs up PostgreSQL, including vectors and receipts.
set -eu
mkdir -p backups
umask 077
docker compose exec -T db pg_dump -U loopp -d loopp -Fc > "backups/loopp-$(date -u +%Y%m%dT%H%M%SZ).dump"
