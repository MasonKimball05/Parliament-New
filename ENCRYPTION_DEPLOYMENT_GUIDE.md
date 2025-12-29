# Field-Level Encryption Deployment Guide

## Overview

This guide explains how to deploy AES field-level encryption for sensitive data in the Parliament application.

### What Gets Encrypted

The following sensitive fields are now encrypted at rest using Fernet (AES-128-CBC + HMAC-SHA256):

**ParliamentUser Model:**
- `username` - Encrypted to protect user identities
- `email` - Encrypted to protect contact information

**LoginHistory Model:**
- `ip_address` - Encrypted to protect user location data

### Encryption Algorithm

We use **django-fernet-fields** which implements **Fernet symmetric encryption**:
- **Algorithm**: AES in CBC mode with 128-bit keys
- **Authentication**: HMAC using SHA256
- **Key Derivation**: Uses cryptography library's Fernet implementation
- **Benefits**:
  - Authenticated encryption (prevents tampering)
  - Industry-standard cryptography
  - Automatic IV generation per encryption
  - Secure against padding oracle attacks

## Deployment Steps

### Step 1: Install Dependencies

```bash
# Install encryption libraries
pip install django-fernet-fields==0.6 cryptography==43.0.3

# Or use requirements.txt
pip install -r requirements.txt
```

### Step 2: Generate Encryption Key

```bash
# Generate a new encryption key
python3 generate_encryption_key.py
```

**IMPORTANT**: This will output a key like:
```
k3eH9fJ2mN8qR7tY5vX1wZ4bC6dF8gH0j_example_key=
```

**Save this key securely!** If you lose it, encrypted data CANNOT be recovered.

### Step 3: Configure Environment Variables

#### Local Development

Add to your `.env` file:
```bash
ENCRYPTION_KEY=your_generated_key_here
```

#### Production Server

SSH into your production server and add the key:

```bash
ssh root@167.99.115.182
cd /var/www/Parliament-New
echo 'ENCRYPTION_KEY=your_generated_key_here' >> .env
```

**CRITICAL**: Use the SAME encryption key on all environments (local, production, staging).

### Step 4: Create and Run Migrations

```bash
# Create migrations for the new encrypted fields
python manage.py makemigrations

# Apply migrations
python manage.py migrate
```

### Step 5: Restart Application

#### Local Development
```bash
# Restart your development server
# If using runserver, just stop and start again
```

#### Production Server
```bash
ssh root@167.99.115.182
cd /var/www/Parliament-New
sudo systemctl restart parliament-gunicorn
```

## How It Works

### Encryption Process

1. When you save data to an encrypted field:
   ```python
   user.username = "mkimball"
   user.save()
   ```

2. django-fernet-fields automatically:
   - Encrypts "mkimball" using the Fernet key
   - Stores encrypted data in database: `gAAAAABf...` (base64-encoded ciphertext)

3. Database stores: `gAAAAABfXHR5cGUiOiAiRmVybmV0IiwgImRhdGEiOiAibWtpbWJhbGwifQ==`

### Decryption Process

1. When you read data from an encrypted field:
   ```python
   username = user.username
   ```

2. django-fernet-fields automatically:
   - Decrypts the ciphertext using the Fernet key
   - Returns plain text: "mkimball"

3. **The application code doesn't change** - encryption/decryption is transparent!

## Security Considerations

### Key Management

**DO:**
- ✅ Store the encryption key in `.env` file (NOT in code)
- ✅ Add `.env` to `.gitignore` (already done)
- ✅ Keep a secure backup of the key (password manager, secure vault)
- ✅ Use the same key across all environments
- ✅ Restrict access to the key (only admins should know it)

**DON'T:**
- ❌ Commit the encryption key to version control
- ❌ Share the key in plain text emails/messages
- ❌ Use different keys on different servers
- ❌ Lose the key (data will be PERMANENTLY unrecoverable)

### What's Protected

| Data | Encrypted at Rest | Encrypted in Transit | Protected From |
|------|-------------------|---------------------|----------------|
| Usernames | ✅ Yes | ✅ Yes (SSL/TLS) | Database breaches, disk theft |
| Emails | ✅ Yes | ✅ Yes (SSL/TLS) | Database breaches, disk theft |
| Login IPs | ✅ Yes | ✅ Yes (SSL/TLS) | Database breaches, disk theft |
| Passwords | ✅ Yes (hashed) | ✅ Yes (SSL/TLS) | Database breaches, rainbow tables |

### Limitations

**What encryption DOES protect:**
- ✅ Data at rest (if someone steals the database file)
- ✅ Database dumps without the encryption key
- ✅ Unauthorized database access (without the key)

**What encryption DOESN'T protect:**
- ❌ Application-level access (if someone hacks the running application)
- ❌ SQL injection (encrypt doesn't prevent SQL injection)
- ❌ Memory dumps while application is running

## Performance Considerations

### Impact

- **Encryption overhead**: ~0.1-0.5ms per field
- **Storage overhead**: ~30-50% larger (base64 encoding)
- **Query performance**: Encrypted fields cannot be efficiently searched/indexed

### Best Practices

**DO:**
- ✅ Only encrypt truly sensitive data
- ✅ Use regular fields for data that needs to be searchable
- ✅ Keep `user_id` and `name` unencrypted for searching

**DON'T:**
- ❌ Encrypt fields you need to search/filter on
- ❌ Encrypt every field "just in case"
- ❌ Use encrypted fields as foreign keys

## Backup and Recovery

### Backup the Encryption Key

1. Save the encryption key to a password manager:
   ```
   Service: Parliament Encryption Key
   Key: your_key_here
   ```

2. Create an encrypted backup file:
   ```bash
   echo "ENCRYPTION_KEY=your_key_here" > parliament_encryption_key.txt
   gpg -c parliament_encryption_key.txt  # Encrypt with password
   rm parliament_encryption_key.txt      # Delete plaintext
   ```

### Rotating Encryption Keys

If you need to rotate the encryption key (e.g., after a security incident):

1. Generate a new key:
   ```bash
   python3 generate_encryption_key.py
   ```

2. Update settings to support both keys:
   ```python
   FERNET_KEYS = [
       os.getenv('NEW_ENCRYPTION_KEY').encode(),  # New key (index 0)
       os.getenv('OLD_ENCRYPTION_KEY').encode(),  # Old key (index 1)
   ]
   ```

3. Create a data migration to re-encrypt all data with the new key

4. Remove the old key after migration is complete

## Troubleshooting

### Error: "ENCRYPTION_KEY must be set in production environment"

**Cause**: The ENCRYPTION_KEY is not set in the .env file

**Solution**:
```bash
# Generate a new key
python3 generate_encryption_key.py

# Add to .env file
echo 'ENCRYPTION_KEY=your_generated_key_here' >> .env
```

### Error: "Fernet key must be 32 url-safe base64-encoded bytes"

**Cause**: Invalid encryption key format

**Solution**: Generate a new valid key using the provided script:
```bash
python3 generate_encryption_key.py
```

### Error: "cryptography.fernet.InvalidToken"

**Possible causes**:
1. **Wrong encryption key** - You're using a different key than the one used to encrypt
2. **Corrupted data** - The encrypted data was modified
3. **Key rotation** - The key was changed without migrating data

**Solutions**:
1. Ensure you're using the correct encryption key from when data was encrypted
2. Check if data was manually modified in the database
3. If key was changed, restore the old key or re-encrypt data

### Performance Issues

**Symptom**: Slow queries on encrypted fields

**Solution**:
- Don't query/filter on encrypted fields
- Use a separate unencrypted field for searching if needed
- Example: Store encrypted email AND a hashed email for lookups

## Testing

### Verify Encryption is Working

```python
# In Django shell (python manage.py shell)
from src.models import ParliamentUser

# Create a test user
user = ParliamentUser.objects.create(
    user_id='TEST001',
    name='Test User',
    username='testuser',
    email='test@example.com',
    member_type='Member'
)

# Check that data is readable in code
print(user.username)  # Should print: testuser
print(user.email)     # Should print: test@example.com

# Check that data is encrypted in database
from django.db import connection
cursor = connection.cursor()
cursor.execute("SELECT username, email FROM src_parliamentuser WHERE user_id='TEST001'")
row = cursor.fetchone()
print(row)  # Should print encrypted data like: ('gAAAAABf...', 'gAAAAABf...')

# Clean up
user.delete()
```

Expected output:
```
testuser
test@example.com
('gAAAAABfXHR5cGUiOiAiRmVybmV0IiwgImRhdGEiOiAidGVzdHVzZXIifQ==', 'gAAAAABfXHR5cGUiOiAiRmVybmV0IiwgImRhdGEiOiAidGVzdEBleGFtcGxlLmNvbSJ9')
```

## Additional Resources

- [Fernet Specification](https://github.com/fernet/spec/)
- [django-fernet-fields Documentation](https://django-fernet-fields.readthedocs.io/)
- [Cryptography Library](https://cryptography.io/en/latest/)

## Support

If you encounter issues:
1. Check this troubleshooting section
2. Verify your encryption key is correct
3. Check Django logs for detailed error messages
4. Ensure all dependencies are installed: `pip install -r requirements.txt`
