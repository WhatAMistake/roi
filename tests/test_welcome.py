#!/usr/bin/env python3
"""Test script to verify the updated welcome messages."""

import sys
sys.path.insert(0, 'src')

from i18n import t

print("=" * 60)
print("TESTING UPDATED WELCOME MESSAGES")
print("=" * 60)

print("\n=== RUSSIAN WELCOME ===")
ru_welcome = t('ru', 'welcome', name='Алексей')
print(ru_welcome)
print("\nChecking for /help mention:", "/help" in ru_welcome)

print("\n=== ENGLISH WELCOME ===")
en_welcome = t('en', 'welcome', name='Alex')
print(en_welcome)
print("\nChecking for /help mention:", "/help" in en_welcome)

print("\n" + "=" * 60)
if "/help" in ru_welcome and "/help" in en_welcome:
    print("✅ SUCCESS: Both welcome messages now include /help reference!")
else:
    print("❌ FAILED: /help reference missing in one or both messages")
print("=" * 60)
