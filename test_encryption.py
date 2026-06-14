#!/usr/bin/env python3
"""Test script to diagnose encryption issues."""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load .env from current directory
env_path = Path(__file__).parent / ".env"
print(f"Looking for .env at: {env_path}")
print(f"File exists: {env_path.exists()}")

if env_path.exists():
    load_dotenv(dotenv_path=env_path, override=True)
    print("Loaded .env file")
else:
    load_dotenv()
    print("Tried to load .env from default locations")

# Check for the key
key = os.getenv("USER_PREFS_ENCRYPTION_KEY")
print(f"\nUSER_PREFS_ENCRYPTION_KEY: {'FOUND' if key else 'NOT FOUND'}")

if key:
    print(f"Key length: {len(key)} characters")
    print(f"Key preview: {key[:10]}...{key[-10:]}")
    
    # Check for quotes
    if key.startswith('"') or key.startswith("'"):
        print("WARNING: Key starts with quote!")
    if key.endswith('"') or key.endswith("'"):
        print("WARNING: Key ends with quote!")
    
    # Try to validate
    try:
        import base64
        decoded = base64.urlsafe_b64decode(key)
        print(f"Base64 decoded length: {len(decoded)} bytes")
        if len(decoded) == 32:
            print("✅ Key is VALID (32 bytes)")
        else:
            print(f"❌ Key is INVALID (expected 32 bytes, got {len(decoded)})")
    except Exception as e:
        print(f"❌ Key decode error: {e}")
else:
    print("\nPossible issues:")
    print("1. .env file doesn't exist in project root")
    print("2. Variable name is misspelled")
    print("3. Variable is commented out with #")
    print("\nTo generate a valid key, run:")
    print('python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"')

# Check cryptography
try:
    from cryptography.fernet import Fernet
    print("\n✅ cryptography package is installed")
except ImportError:
    print("\n❌ cryptography package is NOT installed")
    print("Install with: pip install cryptography")
