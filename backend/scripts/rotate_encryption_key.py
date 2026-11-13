"""Re-encrypt every Fernet-encrypted column in the DB with a new key.

Run when you need to rotate ``ENCRYPTION_KEY``. Without this script the
only rotation path is "clear the saved credentials row and re-enter
them through the settings page" — which loses your client secret if
you don't have it written down somewhere else.

What gets rotated (every Fernet-encrypted value, in one pass):
* ``azure_credentials.client_secret_encrypted`` — the Azure SP client
  secret pasted into Settings → Credentials.
* ``app_settings['ai'].key_encrypted`` — the Anthropic API key pasted into
  Settings → Advice → AI wording.

Usage::

    # 1. generate a new key
    python -c "from cryptography.fernet import Fernet; \\
               print(Fernet.generate_key().decode())"

    # 2. inside the backend container, with the *current* key still in
    #    ENCRYPTION_KEY, run a dry run first
    docker compose exec -e OLD_KEY=$ENCRYPTION_KEY -e NEW_KEY=<new> \\
        backend python -m scripts.rotate_encryption_key --dry-run

    # 3. when the dry run looks good, run it for real
    docker compose exec -e OLD_KEY=$ENCRYPTION_KEY -e NEW_KEY=<new> \\
        backend python -m scripts.rotate_encryption_key

    # 4. swap ENCRYPTION_KEY in your .env to the new value and
    #    recreate the backend container so the app picks it up
    docker compose up -d --force-recreate backend

Safety:
* If any row fails to decrypt with the old key, the whole rotation
  aborts before writing anything — half-rotated state is worse than
  no rotation.
* ``--dry-run`` reports the row count and any decryption failures
  without touching the DB.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from app.core.ai_wording import SETTINGS_KEY as AI_SETTINGS_KEY
from app.db.models import AppSetting, AzureCredentials
from app.db.session import SessionLocal

logger = logging.getLogger("rotate_encryption_key")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--old-key",
        default=os.environ.get("OLD_KEY"),
        help="Current Fernet key. Defaults to $OLD_KEY.",
    )
    parser.add_argument(
        "--new-key",
        default=os.environ.get("NEW_KEY"),
        help="New Fernet key. Defaults to $NEW_KEY.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Decrypt + re-encrypt in memory; do not write.",
    )
    args = parser.parse_args()
    if not args.old_key or not args.new_key:
        parser.error(
            "--old-key and --new-key are both required (or set OLD_KEY / "
            "NEW_KEY in env). Generate a new key with: "
            "python -c 'from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())'"
        )
    if args.old_key == args.new_key:
        parser.error("--old-key and --new-key must differ")
    return args


def _fernet(key: str, label: str) -> Fernet:
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        sys.exit(
            f"error: {label} is not a valid Fernet key (must be 44-char url-safe base64): {exc}"
        )


Secret = tuple[str, bytes, Callable[[bytes], None]]


def _secrets(session: Session) -> list[Secret]:
    """``(label, ciphertext, store)`` for every encrypted value in the database."""
    out: list[Secret] = []
    for row in (
        session.query(AzureCredentials)
        .filter(AzureCredentials.client_secret_encrypted.is_not(None))
        .all()
    ):

        def store_azure(token: bytes, row: AzureCredentials = row) -> None:
            row.client_secret_encrypted = token

        out.append((f"azure_credentials id={row.id}", row.client_secret_encrypted, store_azure))
    ai = session.get(AppSetting, AI_SETTINGS_KEY)
    if ai is not None and (ai.value or {}).get("key_encrypted"):

        def store_ai(token: bytes, row: AppSetting = ai) -> None:
            row.value = {
                **(row.value or {}),
                "key_encrypted": token.decode(),
            }  # a new dict, so it is saved

        out.append(("app_settings ai.key_encrypted", ai.value["key_encrypted"].encode(), store_ai))
    return out


def rotate(session: Session, old: Fernet, new: Fernet, dry_run: bool = False) -> int:
    """Re-encrypt every secret, or nothing: returns 0 (done or nothing to do) or 2 (a value failed)."""
    secrets = _secrets(session)
    if not secrets:
        logger.info("no encrypted values — nothing to rotate")
        return 0

    # Phase 1: decrypt everything in memory; fail fast so nothing is half-rotated.
    plaintexts: list[tuple[Callable[[bytes], None], bytes]] = []
    failures = 0
    for label, token, store in secrets:
        try:
            plaintexts.append((store, old.decrypt(token)))
        except InvalidToken:
            logger.error("%s failed to decrypt — wrong --old-key?", label)
            failures += 1
    if failures:
        logger.error("aborting: %d value(s) could not be decrypted", failures)
        return 2

    logger.info(
        "ready to rotate %d value(s): %s", len(secrets), ", ".join(label for label, _, _ in secrets)
    )
    if dry_run:
        logger.info("dry-run mode: no changes written")
        return 0

    # Phase 2: re-encrypt and persist, in one transaction.
    for store, plaintext in plaintexts:
        store(new.encrypt(plaintext))
    session.commit()
    logger.info(
        "rotated %d value(s). Update ENCRYPTION_KEY to the new value and recreate the backend container.",
        len(plaintexts),
    )
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = _parse_args()
    old = _fernet(args.old_key, "--old-key")
    new = _fernet(args.new_key, "--new-key")
    with SessionLocal() as session:
        return rotate(session, old, new, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
