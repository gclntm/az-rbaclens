"""Turn sign-in off from the host: the way back in after a wrong sign-in setting.

The sign-in settings are admins only once sign-in is on, so a wrong tenant,
client id or admin group could lock everyone out. Shell access to the host
is the only way to undo that; network access to the app is not enough.

Usage (``make auth-off <env>`` runs this inside the backend container)::

    python -m scripts.auth_off

Keeps the tenant, client and group ids, so turning sign-in back on in
Settings › Authentication restores them.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.security import load_auth_settings
from app.db.session import SessionLocal


def main() -> int:
    with SessionLocal() as session:
        row = load_auth_settings(session)
        if row is None or not row.enabled:
            print("Sign-in is already off.")
            return 0
        row.enabled = False
        row.updated_at = datetime.now(UTC)
        session.commit()
    print("Sign-in is now off. The tenant, client and group ids are kept.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
