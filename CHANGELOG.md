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

### v2.4.0 - Bug Report System & Email Service Update (01-19-2026)
Comprehensive bug reporting system with public tracker and admin management, plus migration to Brevo for reliable email delivery.

**Deployment Status:** ⏳ **Pending deployment** - Ready for production

**Key Features:**
- 🐛 **Bug Report System**: Full-featured bug tracking with submission form, public tracker, and admin management
- 📧 **Brevo Email Integration**: API-based email delivery replacing blocked SMTP ports
- 🔧 **Bug Fixes**: Fixed IP address handling, settings configuration, and ActivityLog field names

**[📄 View Detailed Changelog](./changelogs/v2.4.0.md)**

---

### v2.3.0 - Calendar Subscriptions & Admin v2 System (01-06-2026)
Major feature additions including auto-updating calendar subscriptions, advanced administration panel, and site-wide feature flag system.

**Deployment Status:** ✅ **Deployed**

**Key Features:**
- 📅 **Calendar Subscriptions**: Auto-updating calendar feeds for Google Calendar, Apple Calendar, Outlook, and more
- 🔧 **Admin v2**: Advanced administration dashboard with dual authentication and site-wide controls
- 🎛️ **Feature Flags System**: Granular control over site features without code changes
- 🔒 **Enhanced Security**: Token-based calendar access, comprehensive audit logging
- 💬 **Chat Feature Protection**: All chat functionality now respects feature flags

**[📄 View Detailed Changelog](./changelogs/v2.3.0.md)**

---

### v2.2.0 - Reference Documentation System (12-25-2025)
Added comprehensive reference pages for Robert's Rules and Constitution & Bylaws with navigation and search.

**Deployment Status:** ✅ **Deployed**

**Key Features:**
- 📖 **Robert's Rules Reference Page**: Comprehensive parliamentary procedure guide with 10 sections
- 📜 **Constitution & Bylaws Page**: Complete chapter governing documents with cross-references
- 🔍 **Live Search**: Real-time text search with highlighting on both pages
- 📑 **Table of Contents**: Sticky sidebar with active section tracking
- 🔗 **Cross-References**: Links between related content in both documents
- 📊 **Quick Reference Tables**: Motion charts, voting requirements, and GPA levels
- 📱 **Responsive Design**: Optimized for desktop, tablet, and mobile

**[📄 View Detailed Changelog](./changelogs/v2.2.0.md)**

---

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

**Deployment Status:** ✅ **Deployed** 

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

- **[v2.4.0 - Bug Report System & Email Service Update](./changelogs/v2.4.0.md)** (January 19, 2026)
  - Bug report system implementation
  - Public bug tracker and admin management
  - Brevo email integration
  - Bug fixes and infrastructure updates
  - Deployment guide

- **[v2.3.0 - Calendar Subscriptions & Admin v2 System](./changelogs/v2.3.0.md)** (January 6, 2026)
  - Complete feature documentation
  - Calendar subscription system implementation
  - Admin v2 dashboard and authentication
  - Feature flags system architecture
  - Security considerations and deployment guide
  - API documentation

- **[v2.2.0 - Robert's Rules Reference System](./changelogs/v2.2.0.md)** (December 26, 2025)
  - Complete feature documentation
  - Technical implementation details
  - Content coverage and organization
  - JavaScript functionality
  - Deployment guide
  - Browser compatibility

- **[v2.1.0 - Security & Authentication Enhancements](./changelogs/v2.1.0.md)** (December 26, 2025)
  - Complete feature documentation
  - Technical implementation details
  - Deployment guide
  - Security metrics
  - Testing documentation

*Note: Detailed changelogs for v1.0.0 and v2.0.0 were not created as they preceded the structured changelog system.*

---

## Version History Summary

- **v2.4.0** (2026-01-19) - Bug Report System & Email Service Update ⏳
  - Comprehensive bug reporting system with user submissions
  - Public bug tracker with filtering and status tracking
  - Admin-only bug management dashboard (user_id 73)
  - Brevo API email integration (replaces blocked SMTP)
  - Fixed IP address handling for X-Forwarded-For headers
  - Fixed WSGI/ASGI settings module configuration

- **v2.3.0** (2026-01-06) - Calendar Subscriptions & Admin v2 System ✅
  - Auto-updating calendar subscription feeds with secure tokens
  - Admin v2 advanced administration dashboard
  - Site-wide feature flags system with granular control
  - Page toggles to enable/disable specific pages
  - Chat system feature flag protection
  - Enhanced security and comprehensive audit logging

- **v2.2.0** (2025-12-26) - Reference Documentation System ✅
  - Robert's Rules of Order reference page (10 comprehensive sections)
  - Constitution & Bylaws reference page (complete governing documents)
  - Live search with text highlighting on both pages
  - Table of contents with active section tracking
  - Cross-references linking related content between documents
  - Quick reference tables for motions, voting, and GPA levels
  - Responsive design for all devices

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

**Last Updated:** 2026-01-19
**Next Review:** 2026-02-19
