#!/usr/bin/env python3
"""
Generate a secure encryption key for django-fernet-fields

This key should be:
1. Stored in the .env file as ENCRYPTION_KEY
2. Kept secret and never committed to version control
3. Backed up securely (losing this key means losing access to encrypted data)

Usage:
    python3 generate_encryption_key.py
"""

from cryptography.fernet import Fernet

def generate_key():
    """Generate a new Fernet encryption key"""
    key = Fernet.generate_key()
    return key.decode('utf-8')

if __name__ == '__main__':
    key = generate_key()
    print("=" * 80)
    print("ENCRYPTION KEY GENERATED")
    print("=" * 80)
    print("\nYour encryption key:")
    print(f"\n{key}\n")
    print("=" * 80)
    print("IMPORTANT INSTRUCTIONS:")
    print("=" * 80)
    print("\n1. Add this line to your .env file:")
    print(f"   ENCRYPTION_KEY={key}")
    print("\n2. NEVER commit the .env file to version control")
    print("\n3. Keep a secure backup of this key")
    print("   - Store it in a password manager")
    print("   - If you lose this key, encrypted data CANNOT be recovered")
    print("\n4. Use the same key on all servers (local, production, etc.)")
    print("\n5. For production, also add to the server's .env file:")
    print(f"   ssh root@167.99.115.182")
    print(f"   cd /var/www/Parliament-New")
    print(f"   echo 'ENCRYPTION_KEY={key}' >> .env")
    print("\n" + "=" * 80)
