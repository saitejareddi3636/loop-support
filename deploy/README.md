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

Build the frontend with `cd frontend && npm ci && npm run build`. Authenticate
Cloudflare, set its three production bindings, then use
`npx wrangler pages deploy dist --project-name loopp-support --branch main`.
Pages uses direct uploads; GitHub pushes do not automatically publish it.

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

28 focused tests and the React production build pass. Public API checks cover
SSE, real tools, active policy citations, cookies/isolation, and fresh chats.
A browser confirmation issued the fictional INV-3001 receipt; a repeated request
must return that receipt. INV-3002 was staged only. INV-3005 ($89 USD) remains
paid and unused for `sam.patel@example.test`, subject to normal date/eligibility
checks. Seed dates are fixed at November 21, 2026; the Business window is 14 days.
Local demo receipts were not copied to the new production database.

Public WSS checks verify Gemini Live native audio, agent tool routing, reconnect,
and shared history using synthetic text input. Physical microphone input,
speaker playback, mute/unmute, and spoken interruption require a human browser
check and are not claimed as tested.
