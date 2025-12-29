#!/usr/bin/env python3
"""
Migrate existing plaintext usernames and emails to encrypted format.

This script should be run ONCE after deploying encrypted fields.
It encrypts all existing plaintext data in the database.
"""
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'Parliament.settings_postgres')
django.setup()

from django.db import connection
from cryptography.fernet import Fernet
from django.conf import settings

def get_fernet():
    """Get Fernet cipher from settings"""
    key = settings.CRYPTOGRAPHY_KEY
    if not key:
        raise ValueError("CRYPTOGRAPHY_KEY not set in settings")
    return Fernet(key)

def encrypt_value(value):
    """Encrypt a single value"""
    if not value:
        return value
    fernet = get_fernet()
    encrypted = fernet.encrypt(str(value).encode('utf-8'))
    return encrypted.decode('utf-8')

def is_encrypted(value):
    """Check if a value is already encrypted (Fernet format check)"""
    if not value or len(value) == 0:
        return False
    # Fernet tokens are base64 and typically much longer than plaintext
    # and contain specific characters
    try:
        # Try to decrypt - if it works, it's encrypted
        fernet = get_fernet()
        fernet.decrypt(value.encode('utf-8'))
        return True
    except:
        return False

def migrate_users():
    """Migrate user data to encrypted format"""
    print("=" * 80)
    print("MIGRATING USER DATA TO ENCRYPTED FORMAT")
    print("=" * 80)

    with connection.cursor() as cursor:
        # Get all users with plaintext data
        cursor.execute("""
            SELECT user_id, username, email
            FROM src_parliamentuser
        """)

        users = cursor.fetchall()
        print(f"\nFound {len(users)} users to check")

        encrypted_count = 0
        skipped_count = 0

        for user_id, username, email in users:
            needs_update = False
            new_username = username
            new_email = email

            # Check and encrypt username
            if username and not is_encrypted(username):
                new_username = encrypt_value(username)
                needs_update = True
                print(f"  Encrypting username for user ID {user_id}: {username}")
            else:
                print(f"  User ID {user_id} username already encrypted, skipping")
                skipped_count += 1

            # Check and encrypt email
            if email and not is_encrypted(email):
                new_email = encrypt_value(email)
                needs_update = True
                print(f"  Encrypting email for user ID {user_id}")

            # Update if needed
            if needs_update:
                cursor.execute("""
                    UPDATE src_parliamentuser
                    SET username = %s, email = %s
                    WHERE user_id = %s
                """, [new_username, new_email, user_id])
                encrypted_count += 1

        print(f"\n✅ Migration complete!")
        print(f"   Encrypted: {encrypted_count} users")
        print(f"   Skipped (already encrypted): {skipped_count} users")

def migrate_login_history():
    """Migrate login history IP addresses to encrypted format"""
    print("\n" + "=" * 80)
    print("MIGRATING LOGIN HISTORY TO ENCRYPTED FORMAT")
    print("=" * 80)

    with connection.cursor() as cursor:
        # Get all login history with plaintext IPs
        cursor.execute("""
            SELECT id, ip_address
            FROM src_loginhistory
        """)

        history = cursor.fetchall()
        print(f"\nFound {len(history)} login history records to check")

        encrypted_count = 0
        skipped_count = 0

        for record_id, ip_address in history:
            if ip_address and not is_encrypted(ip_address):
                new_ip = encrypt_value(ip_address)
                cursor.execute("""
                    UPDATE src_loginhistory
                    SET ip_address = %s
                    WHERE id = %s
                """, [new_ip, record_id])
                encrypted_count += 1
                if encrypted_count % 100 == 0:
                    print(f"  Encrypted {encrypted_count} records...")
            else:
                skipped_count += 1

        print(f"\n✅ Migration complete!")
        print(f"   Encrypted: {encrypted_count} records")
        print(f"   Skipped (already encrypted): {skipped_count} records")

if __name__ == '__main__':
    try:
        print("\n⚠️  WARNING: This will modify your database!")
        print("   Make sure you have a backup before proceeding.\n")

        response = input("Do you want to continue? (yes/no): ").strip().lower()
        if response != 'yes':
            print("Migration cancelled.")
            sys.exit(0)

        migrate_users()
        migrate_login_history()

        print("\n" + "=" * 80)
        print("ALL MIGRATIONS COMPLETE")
        print("=" * 80)
        print("\nYou can now log in with your username and password.")
        print("The data is now encrypted in the database.")

    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
