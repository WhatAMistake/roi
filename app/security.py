"""Shared validation of the user-preferences encryption key."""

import base64
import os
from typing import Optional


def get_encryption_key() -> Optional[bytes]:
    """Get and validate a canonical Fernet key without logging key material."""
    key = os.getenv("USER_PREFS_ENCRYPTION_KEY")
    if not key:
        print("[ENCRYPTION] No USER_PREFS_ENCRYPTION_KEY found in environment")
        return None
    key = key.strip().strip('"').strip("'").strip()
    if not key:
        print("[ENCRYPTION] Empty USER_PREFS_ENCRYPTION_KEY in environment")
        return None
    try:
        from cryptography.fernet import Fernet
        padding_needed = 4 - len(key) % 4
        if padding_needed != 4:
            key = key + '=' * padding_needed
        decoded = base64.urlsafe_b64decode(key.encode('ascii'))
        if len(decoded) == 32:
            canonical_key = base64.urlsafe_b64encode(decoded)
            Fernet(canonical_key)
            print("[ENCRYPTION] Key validated successfully")
            return canonical_key
        print(f"[ENCRYPTION] Invalid key length: {len(decoded)} bytes (expected 32)")
        return None
    except Exception as e:
        print(f"[ENCRYPTION] Invalid key format: {type(e).__name__}")
        return None
