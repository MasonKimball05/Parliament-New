#!/usr/bin/env python3
"""
Decrypt usernames back to plaintext for authentication compatibility.

Usernames cannot be encrypted with non-deterministic encryption (Fernet)
because Django's authentication system needs to perform database lookups.
"""
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Parliament.settings_postgres')
django.setup()

from django.db import connection
from cryptography.fernet import Fernet
from django.conf import settings

def get_fernet():
    key = settings.CRYPTOGRAPHY_KEY
    if not key:
        raise ValueError("CRYPTOGRAPHY_KEY not set")
    return Fernet(key)

def decrypt_usernames():
    print("=" * 80)
    print("DECRYPTING USERNAMES TO PLAINTEXT")
    print("=" * 80)
    print("\nNote: Usernames must be plaintext for Django authentication to work.")
    print("Emails and IP addresses will remain encrypted.\n")

    fernet = get_fernet()

    with connection.cursor() as cursor:
        cursor.execute("SELECT user_id, username FROM src_parliamentuser")
        users = cursor.fetchall()

        print(f"Found {len(users)} users to decrypt")

        decrypted_count = 0
        skipped_count = 0

        for user_id, username in users:
            try:
                # Try to decrypt - if it works, it's encrypted
                decrypted = fernet.decrypt(username.encode('utf-8')).decode('utf-8')
                cursor.execute(
                    "UPDATE src_parliamentuser SET username = %s WHERE user_id = %s",
                    [decrypted, user_id]
                )
                print(f"  Decrypted user {user_id}: {username[:30]}... → {decrypted}")
                decrypted_count += 1
            except:
                # Already plaintext, skip
                print(f"  User {user_id} already plaintext: {username}")
                skipped_count += 1

        print(f"\n✅ Complete!")
        print(f"   Decrypted: {decrypted_count}")
        print(f"   Skipped (already plaintext): {skipped_count}")

if __name__ == '__main__':
    try:
        decrypt_usernames()
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
