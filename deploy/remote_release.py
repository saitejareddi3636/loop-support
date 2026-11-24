#!/usr/bin/env python3
"""Safe Oracle host release for loopp-support.

Runs on the production VM under /opt/loopp-support. Expects a source archive at
/tmp/loopp-<short>-source.tar.gz and LOOPP_RELEASE_SHA set to the full commit.

Preserves: PostgreSQL volume, .env.production, TLS material, proxy/db containers,
and paused legacy loop-ai containers. Rebuilds and restarts only the API service.
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path("/opt/loopp-support")
REVISION = os.environ.get("LOOPP_RELEASE_SHA", "").strip()
if len(REVISION) < 7:
    sys.exit("LOOPP_RELEASE_SHA must be the full git commit sha")
SHORT = REVISION[:7]
ARCHIVE = Path(f"/tmp/loopp-{SHORT}-source.tar.gz")
COMPOSE = [
    "docker",
    "compose",
    "--env-file",
    ".env.production",
    "-f",
    "compose.production.yaml",
    "-p",
    "loopp-support",
]
PROTECTED = [
    "loopp-support-db-1",
    "loopp-support-proxy-1",
    "loopp-api",
    "loopp-caddy",
    "loopp-db",
]
STATE_CODE = """import json
from app.db import connect
with connect() as db:
    receipts=db.execute('SELECT receipt_id,invoice_id,amount_cents FROM refund_receipts ORDER BY receipt_id').fetchall()
    counts={t:db.execute('SELECT count(*) AS n FROM '+t).fetchone()['n'] for t in ['customers','invoices','chats','refund_receipts','escalation_tickets','knowledge_chunks']}
print(json.dumps({'receipts':receipts,'counts':counts}, default=str))
"""


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def output(args):
    return subprocess.check_output(args, text=True).strip()


def identity(name):
    return output(
        [
            "docker",
            "inspect",
            "--format",
            "{{.Id}} {{.Image}} {{.State.Status}} {{.HostConfig.RestartPolicy.Name}}",
            name,
        ]
    )


def state():
    return json.loads(output(COMPOSE + ["exec", "-T", "api", "python", "-c", STATE_CODE]))


def main():
    if not ARCHIVE.is_file():
        sys.exit(f"missing source archive {ARCHIVE}")
    os.chdir(ROOT)
    os.umask(0o077)

    before = {name: identity(name) for name in PROTECTED}
    old_image = output(["docker", "inspect", "--format", "{{.Image}}", "loopp-support-api-1"])
    run(["docker", "tag", old_image, f"loopp-support-api:rollback-{SHORT}"])

    prior = state()
    backup = ROOT / "backups" / (
        f"pre-release-{SHORT}-"
        f"{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.dump"
    )
    backup.parent.mkdir(mode=0o700, exist_ok=True)
    with backup.open("wb") as target:
        run(COMPOSE + ["exec", "-T", "db", "pg_dump", "-U", "loopp_admin", "-d", "loopp", "-Fc"], stdout=target)
        target.flush()
        os.fsync(target.fileno())
    if backup.stat().st_size <= 1024:
        sys.exit("database backup is unexpectedly small")
    print(f"Protected database backup created; existing receipts: {len(prior['receipts'])}", flush=True)

    stage = ROOT / "releases" / SHORT
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True, mode=0o750)
    run(["tar", "-xzf", str(ARCHIVE), "-C", str(stage)])

    (ROOT / "runtime" / "releases").mkdir(parents=True, exist_ok=True)
    manifest_path = ROOT / "runtime" / "releases" / f"{SHORT}.json"
    manifest = {
        "revision": REVISION,
        "previous_image": old_image,
        "untouched_containers": before,
        "before": prior,
        "database_backup": str(backup),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str))
    manifest_path.chmod(0o600)

    run(
        [
            "docker",
            "build",
            f"--label=org.opencontainers.image.revision={REVISION}",
            "-t",
            f"loopp-support-api:release-{SHORT}",
            str(stage / "backend"),
        ]
    )
    old_compose = (ROOT / "compose.production.yaml").read_bytes()
    (manifest_path.parent / f"{SHORT}-previous-compose.yaml").write_bytes(old_compose)

    # Preserve protected env/TLS/data. Only refresh application source trees needed by API.
    shutil.copytree(stage / "backend", ROOT / "backend", dirs_exist_ok=True)
    shutil.copyfile(stage / "compose.production.yaml", ROOT / "compose.production.yaml")
    for filename in [
        "deploy/maintain.sh",
        "deploy/smoke_test.sh",
        "deploy/.env.example",
        "deploy/remote_release.py",
        "README.md",
    ]:
        src = stage / filename
        if src.is_file():
            dest = ROOT / filename
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)
    (ROOT / "deploy" / "maintain.sh").chmod(0o600)
    (ROOT / "deploy" / "smoke_test.sh").chmod(0o755)

    run(["docker", "tag", f"loopp-support-api:release-{SHORT}", "loopp-support-api:latest"])
    try:
        run(COMPOSE + ["up", "-d", "--no-deps", "--wait", "--wait-timeout", "180", "api"])
    except Exception:
        print("API health check failed; restoring previous API image and Compose file.", flush=True)
        (ROOT / "compose.production.yaml").write_bytes(old_compose)
        run(["docker", "tag", old_image, "loopp-support-api:latest"])
        run(COMPOSE + ["up", "-d", "--no-deps", "--wait", "--wait-timeout", "180", "api"])
        raise

    current = state()
    if not all(r in current["receipts"] for r in prior["receipts"]):
        sys.exit("a previously persisted receipt changed")
    if not all(identity(name) == value for name, value in before.items()):
        sys.exit("an unrelated container changed")

    refresh1 = output(COMPOSE + ["exec", "-T", "api", "python", "refresh_demo.py"])
    refresh2 = output(COMPOSE + ["exec", "-T", "api", "python", "refresh_demo.py"])
    if json.loads(refresh2).get("inserted") != []:
        sys.exit("demo refresh is not idempotent")
    mode = output(
        COMPOSE
        + ["exec", "-T", "api", "python", "-c", 'import os; print(os.getenv("RELEVANCE_VERIFIER","on"))']
    )
    if mode != "on":
        sys.exit(f"unexpected verifier mode: {mode}")
    label = output(
        [
            "docker",
            "inspect",
            "--format",
            '{{index .Config.Labels "org.opencontainers.image.revision"}}',
            "loopp-support-api-1",
        ]
    )
    if label != REVISION:
        sys.exit(f"api image revision mismatch: {label}")

    current = state()
    manifest.update(after=current, verifier_mode=mode, refresh=json.loads(refresh1))
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str))
    print(f"API healthy at commit {SHORT} with RELEVANCE_VERIFIER={mode}", flush=True)
    print("Existing receipts preserved. Database, proxy and legacy containers unchanged.", flush=True)
    print("Demo refresh:", refresh1, flush=True)


if __name__ == "__main__":
    main()
