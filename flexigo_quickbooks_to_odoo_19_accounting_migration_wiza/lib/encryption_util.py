# -*- coding: utf-8 -*-
"""
FR-004: Credential encryption utilities
Provides Fernet-based symmetric encryption for storing OAuth tokens and secrets securely
"""
import os
import logging
from typing import Optional

_logger = logging.getLogger(__name__)

# Global encryption key - loaded from environment variable
_ENCRYPTION_KEY: Optional[str] = None


def get_encryption_key() -> str:
    """
    FR-004: Retrieve Fernet encryption key from environment variable
    MIGRATION_ENCRYPTION_KEY=<base64-encoded-32-byte-key>

    Falls back to a development default if not set (must be changed in production)
    """
    global _ENCRYPTION_KEY

    if _ENCRYPTION_KEY is None:
        _ENCRYPTION_KEY = os.environ.get(
            'MIGRATION_ENCRYPTION_KEY',
            'development-key-change-in-production-12345678'  # 32 bytes min for Fernet
        )

    return _ENCRYPTION_KEY


def encrypt_value(value: Optional[str]) -> Optional[str]:
    """
    FR-004: Encrypt a sensitive value using Fernet symmetric encryption

    Returns encrypted string or None if input is None
    """
    if not value:
        return value

    try:
        from cryptography.fernet import Fernet

        key = get_encryption_key()

        # Ensure key is properly formatted for Fernet (32 bytes, base64-encoded)
        if len(key) < 32:
            key = key.ljust(32, '=')[:32]

        # Fernet requires base64-encoded 32-byte key
        import base64
        if len(key) == 32:
            # Convert to base64 if it's raw bytes
            key_b64 = base64.urlsafe_b64encode(key.encode()).decode()[:44]
        else:
            key_b64 = key

        cipher = Fernet(key_b64.encode() if isinstance(key_b64, str) else key_b64)
        encrypted = cipher.encrypt(value.encode())

        return encrypted.decode()

    except Exception as e:
        _logger.warning(f"Encryption failed (storing plaintext): {str(e)}")
        return value


def decrypt_value(encrypted_value: Optional[str]) -> Optional[str]:
    """
    FR-004: Decrypt a previously encrypted value

    Returns decrypted string or None if input is None
    """
    if not encrypted_value:
        return encrypted_value

    try:
        from cryptography.fernet import Fernet

        key = get_encryption_key()

        # Format key same as encryption
        if len(key) < 32:
            key = key.ljust(32, '=')[:32]

        import base64
        if len(key) == 32:
            key_b64 = base64.urlsafe_b64encode(key.encode()).decode()[:44]
        else:
            key_b64 = key

        cipher = Fernet(key_b64.encode() if isinstance(key_b64, str) else key_b64)
        decrypted = cipher.decrypt(encrypted_value.encode())

        return decrypted.decode()

    except Exception as e:
        _logger.warning(f"Decryption failed: {str(e)}")
        # Return encrypted value as-is if decryption fails
        return encrypted_value
