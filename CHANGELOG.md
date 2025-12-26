# Parliament Development Changelog

## Version History Overview

### v1.0.0 - Initial Release - September 2025 (Development Only)
The original Parliament system with basic functionality but significant security vulnerabilities.

**Deployment Status:** Development/Local only - Never deployed to production

**Major Issues:**
- ❌ **Insecure Authentication**: Login used `username + user_id` instead of passwords
- ❌ **Open Host Headers**: `ALLOWED_HOSTS = ['*']` (vulnerable to host header attacks)
- ❌ **Weak Secret Key**: Fallback to `'fallback-secret'` (publicly known)
- ❌ **Limited File Validation**: Only checked file extensions (easily bypassed)
- ❌ **No Password Complexity**: Any password accepted
- ❌ **Minimal Logging**: No audit trail for admin actions
- ❌ **No Rate Limiting**: Unlimited login/reset attempts possible

\**While there were many versions in between 1.0.0 and 2.0.0, they have been left off, largely because most changes were minor and unimpactful until the 2.0 release changes*

### v2.0.0 - Critical Security Overhaul & Production Deployment (12-20-2025)
Complete security rewrite to address fundamental vulnerabilities and prepare for production hosting.

**Deployment Status:** 🚀 **First production deployment** - Uploaded and hosted online at https://am-parliament.org

**Breaking Changes:**
- ✅ **Password-Based Authentication**: Users must set passwords (old user_id auth removed)
- ✅ **Restricted Hosts**: `ALLOWED_HOSTS` must be configured via environment variable
- ✅ **Enforced Secret Key**: Production raises error if SECRET_KEY not set
- ✅ **Passwords Required**: All passwords must meet complexity requirements

**Security Features Added:**
- ✅ **MIME Type Validation**: File upload security (prevents extension spoofing)
- ✅ **Password Complexity**: 9+ chars, uppercase, lowercase, number, symbol
- ✅ **Admin Action Logging**: All impersonation events logged
- ✅ **HTTPS/SSL Headers**: Secure transport layer configuration
- ✅ **Session Security**: Secure cookies, CSRF protection
- ✅ **Database SSL**: Encrypted database connections

**Impact:**
- Users needed to create new passwords
- Configuration changes required for deployment
- All environment variables must be properly set
- No backward compatibility with v1.0.0 authentication

### v2.1.1 - Changelog Organization (12-26-2025)
Improved documentation structure with organized changelog archive.

**Deployment Status:** ✅ **Deployed** - Documentation update only (no code changes)

**Changes:**
- 📁 Created `changelogs/` folder for version-specific details
- 📄 Extracted v2.1.0 details to dedicated file
- 🔗 Updated main CHANGELOG.md with summary and links
- 📝 Added README to changelogs folder for usage instructions

---

### v2.1.0 - Authentication Enhancements (12-26-2025)
Builds on v2.0.0 security foundation with user-facing features and advanced protections.

**Deployment Status:** ⏳ **Pending deployment** - Code complete, awaiting production update

**Key Features:**
- 🔐 **Password Reset System**: Email-based password reset with cryptographic tokens
- 📧 **Email Management**: Users and admins can add/edit email addresses
- 🛡️ **Login Rate Limiting**: IP and username-based brute force protection
- 👁️ **Admin Access Monitoring**: Comprehensive logging of admin panel activity
- 📊 **Enhanced Audit Logging**: Detailed security event tracking

**[📄 View Detailed Changelog](./changelogs/v2.1.0.md)**

---

## Detailed Changelogs

For comprehensive technical details, migration guides, and implementation specifics:

- **[v2.1.0 - Security & Authentication Enhancements](./changelogs/v2.1.0.md)** (December 26, 2025)
  - Complete feature documentation
  - Technical implementation details
  - Deployment guide
  - Security metrics
  - Testing documentation

*Note: Detailed changelogs for v1.0.0 and v2.0.0 were not created as they preceded the structured changelog system.*

---

## Version History Summary

- **v2.1.1** (2025-12-26) - Changelog Organization ✅
  - Created changelogs archive folder
  - Reorganized documentation structure
  - Improved version tracking

- **v2.1.0** (2025-12-26) - Security & Authentication Enhancements ⏳
  - Password reset system with email verification
  - Login rate limiting and brute force protection
  - Admin panel access monitoring
  - Enhanced audit logging
  - Email management for users

- **v2.0.0** (2025-12-22) - Critical Security Overhaul & Production Deployment 🚀
  - **First production deployment to https://am-parliament.org**
  - Password-based authentication (replaced user_id login)
  - MIME type file validation
  - Password complexity requirements
  - Session and cookie security
  - HTTPS/SSL configuration
  - Admin impersonation logging

- **v1.0.0** (2025-09-XX) - Initial Release (Development Only)
  - Basic Parliament functionality
  - Insecure authentication (username + user_id)
  - Limited security measures
  - Foundation for future improvements
  - **Never deployed to production**

---

## How to Use This Changelog

### For Quick Updates
- Check this main file for version summaries
- See deployment status at a glance
- Review key features and breaking changes

### For Technical Details
- Click the detailed changelog links above
- Review implementation specifics
- Follow deployment guides
- Understand security implications

### When Making Changes
New changes will be documented in:
1. Main CHANGELOG.md (summary only)
2. Detailed version file in `changelogs/` folder
3. Update version number and deployment status

---

## Contributors

- [Mason Kimball](https://github.com/MasonKimball05) - Lead Developer

---

**Last Updated:** 2025-12-26
**Next Review:** 2026-01-26
