"""Azure credential resolution.

Resolution order:

  1. ``ARM_CLIENT_ID`` / ``ARM_CLIENT_SECRET`` / ``ARM_TENANT_ID`` env vars.
  2. Encrypted singleton row in ``azure_credentials`` (entered via the
     settings page; secret decrypted with the Fernet ``ENCRYPTION_KEY``).
  3. ``DefaultAzureCredential`` — falls through to the dev's ``az login``,
     pod identity, etc.

Result is cached via :func:`functools.lru_cache`. The settings API calls
``get_credential.cache_clear()`` whenever the DB row changes so the next
scan picks up the new secret.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

from azure.core.pipeline.transport import RequestsTransport
from azure.identity import ClientSecretCredential, DefaultAzureCredential

from app.core.config import settings

logger = logging.getLogger(__name__)


@lru_cache
def get_credential():
    if settings.arm_client_id and settings.arm_client_secret and settings.arm_tenant_id:
        logger.debug("using Azure credentials from environment variables")
        return ClientSecretCredential(
            tenant_id=settings.arm_tenant_id,
            client_id=settings.arm_client_id,
            client_secret=settings.arm_client_secret,
        )

    db_creds = _load_db_credentials()
    if db_creds is not None:
        logger.debug("using Azure credentials from settings DB row")
        return ClientSecretCredential(**db_creds)

    logger.debug("falling back to DefaultAzureCredential")
    return DefaultAzureCredential()


def ca_bundle_path() -> str | None:
    """Return a CA bundle path to trust for outbound TLS, if one is set.

    Checked in order: ``REQUESTS_CA_BUNDLE`` (also what ``requests`` and the
    Azure SDK pick up automatically), then ``SSL_CERT_FILE``. Returns
    ``None`` if neither is set or the path doesn't exist.
    """
    for env in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE"):
        path = os.environ.get(env)
        if path and os.path.isfile(path):
            return path
    return None


def get_azure_transport() -> RequestsTransport:
    """Build a ``RequestsTransport`` pinned to the user's CA bundle.

    In corporate networks with a TLS-intercepting proxy, the proxy's root
    CA needs to be trusted. Setting ``REQUESTS_CA_BUNDLE`` or
    ``SSL_CERT_FILE`` to that CA file makes both the Azure SDK and the
    Graph resolver verify against it.
    """
    ca = ca_bundle_path()
    if ca:
        return RequestsTransport(connection_verify=ca)
    return RequestsTransport()


def _load_db_credentials() -> dict[str, str] | None:
    """Read the encrypted settings row, if all three fields are populated."""
    # Imported lazily to avoid a circular dep between core.azure and db.session.
    from app.core.crypto import decrypt
    from app.db.models import AzureCredentials
    from app.db.session import SessionLocal

    try:
        with SessionLocal() as session:
            row = session.get(AzureCredentials, 1)
    except Exception:
        logger.warning("could not read azure_credentials row", exc_info=True)
        return None

    if row is None or not row.tenant_id or not row.client_id:
        return None
    secret = decrypt(row.client_secret_encrypted)
    if not secret:
        return None
    return {
        "tenant_id": row.tenant_id,
        "client_id": row.client_id,
        "client_secret": secret,
    }
