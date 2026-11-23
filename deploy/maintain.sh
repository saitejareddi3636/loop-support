#!/bin/sh
# Install only for the loopp-support stack. Run as root every six hours.
set -eu
cd /opt/loopp-support
umask 077
mkdir -p backups
docker compose --env-file .env.production -f compose.production.yaml -p loopp-support exec -T proxy caddy reload --config /etc/caddy/Caddyfile --force
stamp=$(date -u +%Y%m%dT%H%M%SZ)
partial="backups/loopp-$stamp.dump.partial"
docker compose --env-file .env.production -f compose.production.yaml -p loopp-support exec -T db pg_dump -U loopp_admin -d loopp -Fc > "$partial"
mv "$partial" "backups/loopp-$stamp.dump"
# Keep the most recent seven days of Loopp backups only.
find backups -name 'loopp-*.dump' -type f -mtime +7 -delete
