#!/bin/sh
# Container entrypoint: wait for DB, run migrations, then start uvicorn.
#
# Compose brings ``db`` and ``backend`` up in parallel; without a wait,
# the backend's first ``alembic upgrade head`` runs against a still-
# booting postgres and the container crash-loops until compose
# coincidentally retries it after the DB is ready. The loop below
# blocks on a cheap ``SELECT 1`` until either the DB answers or the
# 60-second budget elapses (in which case we exit non-zero and let
# compose surface the real error).
set -e

echo "[entrypoint] waiting for database"
python - <<'PY'
import os
import sys
import time

import psycopg

# SQLAlchemy uses ``postgresql+psycopg://``; psycopg itself wants the
# bare ``postgresql://`` scheme. Strip the driver suffix when present.
dsn = os.environ.get("DATABASE_URL", "")
dsn = dsn.replace("postgresql+psycopg://", "postgresql://", 1)
if not dsn:
    print("[entrypoint] DATABASE_URL is empty; skipping wait")
    sys.exit(0)

deadline = time.time() + 60
last_error = None
while time.time() < deadline:
    try:
        with psycopg.connect(dsn, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        print("[entrypoint] database is up")
        sys.exit(0)
    except Exception as exc:
        last_error = f"{type(exc).__name__}: {exc}".strip()
        time.sleep(2)

print(f"[entrypoint] database never became ready: {last_error}", file=sys.stderr)
sys.exit(1)
PY

echo "[entrypoint] running alembic upgrade head"
alembic upgrade head

echo "[entrypoint] starting uvicorn (stage=${STAGE:-test})"
exec "$@"
