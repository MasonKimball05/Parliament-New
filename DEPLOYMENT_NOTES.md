# Password Reset Feature - Deployment Guide

## Summary of Changes

### 1. Added Email Field to User Model
- Added `email` field to ParliamentUser model (optional, unique)
- Migration created: `src/migrations/0020_parliamentuser_email.py`

### 2. Created Password Reset Flow
- Added Django's built-in password reset URLs
- Created 4 password reset templates (request, done, confirm, complete)
- Created email templates (text + HTML)
- Added "Forgot Password?" link to login page

### 3. Configured Email Settings
- Added email configuration to `Parliament/settings_postgres.py`
- Uses console backend for development
- Configurable via environment variables for production

---

## Production Deployment Steps

### Step 1: Push Code to Production
```bash
# From your local machine
git add .
git commit -m "Add password reset functionality with email field"
git push origin main

# On production server
cd /var/www/Parliament-New
git pull origin main
```

### Step 2: Run Migrations
```bash
cd /var/www/Parliament-New
source venv/bin/activate
python3 manage.py makemigrations
python3 manage.py migrate
```

### Step 3: Configure Email Settings in .env

Add these lines to `/var/www/Parliament-New/.env`:

```bash
# Email Configuration for Password Reset
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=your-email@gmail.com
EMAIL_HOST_PASSWORD=your-app-password
DEFAULT_FROM_EMAIL=noreply@am-parliament.org
```

**For Gmail:**
1. Go to Google Account settings
2. Enable 2-factor authentication
3. Generate an "App Password" for Django
4. Use that app password in EMAIL_HOST_PASSWORD

**Alternative Email Providers:**
- **SendGrid**: EMAIL_HOST=smtp.sendgrid.net, EMAIL_PORT=587
- **Mailgun**: EMAIL_HOST=smtp.mailgun.org, EMAIL_PORT=587
- **AWS SES**: EMAIL_HOST=email-smtp.us-east-1.amazonaws.com

### Step 4: Restart Services
```bash
systemctl restart parliament
systemctl restart nginx
```

### Step 5: Add Emails to User Accounts

Users can add their email addresses through the admin panel or profile page.

**Option A: Bulk add emails via Django admin**
1. Go to https://am-parliament.org/admin/
2. Click on "Parliament users"
3. Edit each user and add their email

**Option B: Let users add their own emails**
- Update the profile page to allow users to add/edit their email address

---

## Testing the Feature

1. **Go to login page**: https://am-parliament.org/accounts/login/
2. **Click "Forgot your password?"**
3. **Enter email address** (must be set in user account)
4. **Check email** for reset link
5. **Click link** and set new password
6. **Login** with new password

---

## Important Notes

### Email Field
- Email field is **optional** for now (existing users don't have emails)
- Email must be **unique** (two users can't have the same email)
- Users without email can't use password reset

### Security
- Reset links expire after **1 hour**
- Links are single-use only
- Password reset requires valid email address

### Email Backend
- **Development**: Emails print to console (no actual sending)
- **Production**: Must configure SMTP settings in .env

---

## Adding Email to Profile Page (Future Enhancement)

To let users add their own emails, update `templates/profile.html`:

```html
<div>
    <label>Email Address</label>
    <input type="email" name="email" value="{{ user.email }}">
    <small>Used for password reset</small>
</div>
```

Then update the profile view to handle email updates.

---

## Troubleshooting

### "SMTPAuthenticationError"
- Check EMAIL_HOST_USER and EMAIL_HOST_PASSWORD are correct
- For Gmail, use App Password (not regular password)
- Ensure 2FA is enabled if using Gmail

### "No email sent"
- Check user has email address set
- Check email isn't in spam folder
- Check Django logs: `journalctl -u parliament -n 50`

### "Invalid reset link"
- Link expires after 1 hour
- Link can only be used once
- Request a new reset link

---

## Files Modified

**Models:**
- `src/models.py` - Added email field

**URLs:**
- `src/urls.py` - Added password reset URLs
- `Parliament/urls.py` - Fixed import

**Settings:**
- `Parliament/settings_postgres.py` - Added email configuration

**Templates Created:**
- `templates/registration/password_reset.html`
- `templates/registration/password_reset_done.html`
- `templates/registration/password_reset_confirm.html`
- `templates/registration/password_reset_complete.html`
- `templates/registration/password_reset_email.txt`
- `templates/registration/password_reset_email.html`
- `templates/registration/password_reset_subject.txt`

**Templates Modified:**
- `templates/registration/login.html` - Added "Forgot Password?" link

**Migrations:**
- `src/migrations/0020_parliamentuser_email.py`
