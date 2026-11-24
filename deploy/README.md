# Deployment

The React app is at https://loopp-support.pages.dev. Pages Functions proxy
`/api/*` to `https://129.80.125.203.sslip.io:8443`, without buffering SSE
or terminating WebSockets. Browser cookies remain first-party, Secure,
HttpOnly, and SameSite=Strict. Gemini keys stay on Oracle.

Oracle uses `/opt/loopp-support`, Compose project `loopp-support`, its own
network and `loopp-support_pgdata` volume. API and PostgreSQL have no host ports.
Only the new HTTPS proxy publishes 8443. The `loopp-support-api` Oracle NSG
allows that port from Cloudflare's published IPv4 ranges only. Existing
containers, ports 80/443, proxy routes, and database volumes are unchanged.

The new proxy mounts the existing sslip.io certificate directory read-only.
Certificate renewal remains with the existing Caddy certificate owner; Loopp's
six-hour maintenance job reloads its copy into memory. Moving/removing that
certificate owner requires a replacement certificate and an updated bind path.
PostgreSQL has a separate private CA and a certificate for `db`; application
connections use `sslmode=verify-full`. Renew its server certificate before expiry.

## Paused legacy application on the shared Oracle VM

On November 23, 2026, the legacy Compose project `loop-ai` was paused to free
resources for Loopp. Its containers are `loopp-api`, `loopp-caddy`, and
`loopp-db`. Their original restart policy was `unless-stopped`; the paused
containers have restart policy `no`. The separate `loopp-support` project stays
running. No containers, images, volumes, databases, files, or configuration
were removed.

To verify the paused state on `129.80.125.203`:

```sh
docker inspect loopp-api loopp-caddy loopp-db \
  --format '{{.Name}} | {{.State.Status}} | {{.HostConfig.RestartPolicy.Name}}'
docker ps --format '{{.Names}} | {{.Status}} | {{.Ports}}'
free -h
```

To restore the legacy application later, run these commands on the VM as the
`ubuntu` user (who can access Docker):

```sh
docker update --restart=unless-stopped loopp-db loopp-api loopp-caddy
docker start loopp-db
until [ "$(docker inspect -f '{{.State.Health.Status}}' loopp-db)" = healthy ]; do sleep 2; done
docker start loopp-api
until [ "$(docker inspect -f '{{.State.Health.Status}}' loopp-api)" = healthy ]; do sleep 2; done
docker start loopp-caddy
docker ps --filter label=com.docker.compose.project=loop-ai \
  --format '{{.Names}} | {{.Status}} | {{.Ports}}'
```

The existing container metadata points to
`/home/ubuntu/loop-ai-next/docker-compose.prod.yml` for `loopp-api` and
`/home/ubuntu/loop-ai/docker-compose.prod.yml` for `loopp-db` and
`loopp-caddy`. The exact `docker start` commands above reuse those containers
and their persisted volumes without rebuilding either Compose version. The
legacy Caddy container owns renewal of the certificate mounted read-only by
Loopp's proxy; while it is paused, monitor that certificate's expiration
(currently February 18, 2027) if the pause lasts longer than expected.

## Operations

The production configuration template is `deploy/.env.example`.
Keep `.env.production` mode 0600 and `runtime/` out of Git. It contains dedicated
database passwords, a random rate-limit secret, and `LOOPP_GATEWAY_SECRET`.
Pages has `PUBLIC_ORIGIN`, `API_ORIGIN`, and the matching gateway secret as
production bindings. Preview deployments cannot access production sessions.

From `/opt/loopp-support`, prefix commands with:

```sh
docker compose --env-file .env.production -f compose.production.yaml -p loopp-support
```

Use `build api` for backend changes, `up -d db` to start PostgreSQL, and
`run --rm api sh -c 'python migrate.py && python seed_data.py && python seed_policies.py'`
for migrations and seeds. Then `up -d --wait` starts the services. Seeding preserves
existing receipts and conversations. Never use `down -v` on persisted data.
To recreate this host setup, supply the HTTPS certificate directory, generate
the PostgreSQL CA/server pair under `runtime/postgres` (SAN `DNS:db`, key owned
by container UID 999 with mode 0600), and keep `deploy/init-db.sh` readable (0644).

### CI/CD (GitHub Actions)

Pushes to `main` run `.github/workflows/deploy.yml`:

1. Frontend `npm ci && npm run build`
2. Cloudflare Pages direct upload via Wrangler (`loopp-support`, branch `main`)
3. Oracle host release through SSH + `deploy/remote_release.py` (API-only rebuild with DB backup and rollback)
4. `deploy/smoke_test.sh https://loopp-support.pages.dev`

Configure repository or `production` environment secrets: `CLOUDFLARE_ACCOUNT_ID`,
`CLOUDFLARE_API_TOKEN`, `ORACLE_HOST`, `ORACLE_USER`, `ORACLE_SSH_PRIVATE_KEY`
(optional `ORACLE_SSH_PORT`). Without the Oracle SSH secrets, Pages still deploys
and the Oracle step is skipped.

Manual fallback remains available: build the frontend with
`cd frontend && npm ci && npm run build`, then
`npx wrangler pages deploy dist --project-name loopp-support --branch main`.
Keep Pages production bindings (`PUBLIC_ORIGIN`, `API_ORIGIN`, `LOOPP_GATEWAY_SECRET`)
in Cloudflare only.

`/etc/cron.d/loopp-support` runs `deploy/maintain.sh` every six hours, writes
protected PostgreSQL dumps under `backups/`, and retains seven days. This is
on-VM recovery only; configure encrypted off-VM backup storage separately.
Back up the CA and `.env.production` securely as well. After restoring a dump
into a fresh database, restore the database role credentials and TLS files.

Rotate the Gemini key in Google AI Studio before sharing broadly if it was
previously exposed; rotation has not been verified. Update the protected server
configuration and recreate only this stack's API. Do not put it in Pages vars,
client bundles, URLs, screenshots, or commits. Keep HTTPS and origin checks enabled.

## Checks and demonstration data

29 focused tests and the React production build pass. Public API checks cover
SSE, real tools, active policy citations, cookies/isolation, and fresh chats.
Public browser confirmations have issued receipts for INV-3001 and INV-3005.
Do not reset those receipts to reuse them. Ask `sam.patel@example.test` to list
currently eligible invoices; eligibility and receipts are checked from the live
database. Each refund requires an explicit invoice choice when several qualify,
then its own confirmation click. Seed dates are fixed at November 21, 2026;
the Business window is 14 days. Local demo receipts were not copied to production.

Public WSS checks verify Gemini Live native audio, agent tool routing, reconnect,
and shared history using synthetic text input. Physical microphone input,
speaker playback, mute/unmute, and spoken interruption require a human browser
check and are not claimed as tested.
