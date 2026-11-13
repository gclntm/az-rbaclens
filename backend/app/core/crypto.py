"""Symmetric encryption helper for the Azure client secret stored in DB.

Uses Fernet (AES-128-CBC + HMAC). The key comes from
``settings.encryption_key`` (env var ``ENCRYPTION_KEY``). When the key is
absent or invalid, encryption / decryption return ``None`` so the API
layer can surface a 503 instead of writing plaintext or crashing.
"""

from __future__ import annotations

import logging

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

logger = logging.getLogger(__name__)


def _fernet() -> Fernet | None:
    if not settings.encryption_key:
        return None
    try:
        return Fernet(settings.encryption_key.encode())
    except ValueError:
        logger.warning("ENCRYPTION_KEY is set but invalid (must be 44-char base64)")
        return None


def encryption_available() -> bool:
    return _fernet() is not None


def encrypt(plaintext: str) -> bytes | None:
    """Return the encrypted bytes, or ``None`` if no usable key is configured."""
    f = _fernet()
    if f is None:
        return None
    return f.encrypt(plaintext.encode())


def decrypt(ciphertext: bytes | None) -> str | None:
    """Return the decrypted plaintext, or ``None`` on failure / missing key."""
    if not ciphertext:
        return None
    f = _fernet()
    if f is None:
        return None
    try:
        return f.decrypt(ciphertext).decode()
    except InvalidToken:
        logger.warning("could not decrypt stored secret — wrong ENCRYPTION_KEY?")
        return None
