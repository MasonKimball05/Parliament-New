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

- Complete security rewrite to address fundamental vulnerabilities and prepare for production hosting.
- Included first major UI overhaul, Home Page UI inspired by my.beta.org UI

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


### v2.5.2 - Committee Minutes Editor (01-29-2026)
Extends the Chapter Minutes editor to committees with full editor support, committee-scoped attendance, and flexible permissions.

**Deployment Status:** Pending Deployment

**Key Features:**

- Committee Minutes System: Full editor (motions, section headers, attendance, PDF) scoped to committee members
- Flexible Permissions: Any member can create minutes; chairs, admins, and designated secretaries can edit
- Publish Options: Publish to committee documents with optional "Also publish to Chapter Documents"
- Committee PDF Titles: PDF generation uses "Committee Minutes: {committee name}" heading
- Data Isolation: Chapter minutes views now filter out committee minutes
- Dark Mode: Edit Chat Settings page

**Database Changes:**

- Added `committee` FK (nullable) to `ChapterMinutes` model
- Added `can_take_minutes` to `CommitteePermissions` for designated secretary role
- Migration: `0072_add_committee_to_chapterminutes.py`

**[View Detailed Changelog](./changelogs/v2.5.2.md)**

---

### v2.5.1 - Minutes Editor Bug Fixes & UX Improvements (01-28-2026)
Bug fixes and UX improvements for the Chapter Minutes editor introduced in v2.5.0.

**Deployment Status:** ⏳ **Pending Deployment**

**Bug Fixes:**

- 🔧 **Delete Minutes Permission Error**: Fixed `is_superuser` AttributeError on ParliamentUser — now uses `is_admin`
- 🔧 **Published PDF Not Updating**: Editing published minutes now regenerates the PDF in Chapter Documents
- 🔧 **Text File Display**: Document viewer now supports `.txt`, `.md`, `.csv`, `.log`, and other plain text formats
- 🔧 **Marker Deletion Breaking Sections**: Fixed newline handling that merged adjacent lines when deleting markers
- 🔧 **Section Cards Wrong Order**: Cards now display in order of appearance instead of grouped by type
- 🔧 **File Upload Validation Gap**: Extended upload validation to cover `.log`, `.json`, `.xml` and all text/data MIME types across `file_validation.py`, `settings_postgres.py`, and `CommitteeDocumentForm`

**UX Improvements:**

- 📝 **Section Headers**: Organize minutes into named sections (Officer Reports, Old Business, etc.) with quick presets
- 📝 **Section Enders (Boxed Sections)**: Pair with headers to create boxed sections in PDF with purple styling
- 📝 **Edit History on PDF**: Published minutes PDF shows edit audit trail with timestamps and reasons
- 📝 **Adjourn Meeting Button**: Prominent button at bottom of editor near Publish section
- 📝 **End Time Field**: Record adjournment time separately from start time
- 📝 **Edit Tracking**: Published minutes edits are tracked with editor name, timestamp, and reason

**[📄 View Detailed Changelog](./changelogs/v2.5.1.md)**

---

### v2.5.0 - Chapter Minutes, Announcements & Document Viewer (01-28-2026)
Major feature release introducing the Chapter Minutes system, Announcements, and a unified Document Viewer.

**Deployment Status:** ⏳ **Pending Deployment**

**Key Features:**

- 📋 **Chapter Minutes System**: Full meeting minutes editor with inline motions, votes, attendance tracking, PDF generation, and publishing to Chapter Documents
- 📢 **Announcements System**: Officer-managed announcements with scheduling, member targeting, email tracking, and engagement statistics
- 📄 **Document Viewer**: Unified in-app viewer for PDF, DOCX (HTML conversion), images, and text files across all document types
- 👮 **Officer Dashboard**: Event management, resolution management, report uploads, activity logs, archived events
- 🔒 **IP Address Encryption**: Login history IP addresses stored with EncryptedCharField
- ⏱️ **Committee Voting Timer**: Optional voting deadline for committee legislation

**[📄 View Detailed Changelog](./changelogs/v2.5.0.md)**

---

### v2.4.1 - Minor UI Improvement Updates (01-22-2026)

**Deployment Status:** ✅ **Deployed**

<ul>
<li>Added dark mode fixes to several Constitution & Bylaws pages:
    <ul>
    <li>Passed Legislation (/constitution-bylaws/passed-resolutions/)</li>
    <li>Officer Duties (/constitution-bylaws/officer-duties/)</li>
    <li>Committees (/constitution-bylaws/committees/)</li>
    <li>Kai Procedures (/constitution-bylaws/kai-procedures/)</li>
    <li>Officer Slating & Elections (/constitution-bylaws/slating-elections/)</li>
    <li>Advisors (/constitution-bylaws/advisors/)</li>
    <li>Academic Standards (/constitution-bylaws/academic-standards/)</li>
    </ul>
</li>
</ul>


### v2.4.0 - Bug Report System & Email Service Update (01-19-2026)
Comprehensive bug reporting system with public tracker and admin management, plus migration to Brevo for reliable email delivery.

**Deployment Status:** ✅ **Deployed** 

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

### v2.2.0 - Reference Documentation System (12-29-2025)
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

<ul>
<li><strong><a href="./changelogs/v2.5.2.md">v2.5.2 - Committee Minutes Editor</a></strong> (January 29, 2026)
    <ul>
    <li>Full committee minutes editor with motions, attendance, and PDF export</li>
    <li>Committee-scoped attendance (members + chairs)</li>
    <li>Flexible permissions: any member creates, chairs/admins/secretaries edit</li>
    <li>Publish to committee documents with optional chapter publish</li>
    <li>Committee-specific PDF titles</li>
    <li>Data isolation between chapter and committee minutes</li>
    </ul>
</li>
<li><strong><a href="./changelogs/v2.5.1.md">v2.5.1 - Minutes Editor Bug Fixes & UX Improvements</a></strong> (January 28, 2026)
    <ul>
    <li>Delete minutes permission fix</li>
    <li>Published PDF regeneration on edit</li>
    <li>Text file display in document viewer</li>
    <li>Marker deletion fix</li>
    <li>File upload validation for text/data file types</li>
    <li>Section headers, enders, and boxed PDF sections</li>
    <li>Adjourn button and edit tracking</li>
    </ul>
</li>
<li><strong><a href="./changelogs/v2.5.0.md">v2.5.0 - Chapter Minutes, Announcements & Document Viewer</a></strong> (January 28, 2026)
    <ul>
    <li>Chapter Minutes system with motions, attendance, and PDF export</li>
    <li>Announcements system with scheduling and email tracking</li>
    <li>Unified document viewer (PDF, DOCX, images, text)</li>
    <li>Officer dashboard enhancements</li>
    <li>IP address encryption</li>
    <li>Deployment guide</li>
    </ul>
</li>
<li><strong><a href="./changelogs/v2.4.0.md">v2.4.0 - Bug Report System & Email Service Update</a></strong> (January 19, 2026)
    <ul>
    <li>Bug report system implementation</li>
    <li>Public bug tracker and admin management</li>
    <li>Brevo email integration</li>
    <li>Bug fixes and infrastructure updates</li>
    <li>Deployment guide</li>
    </ul>
</li>
<li><strong><a href="./changelogs/v2.3.0.md">v2.3.0 - Calendar Subscriptions & Admin v2 System</a></strong> (January 6, 2026)
    <ul>
    <li>Complete feature documentation</li>
    <li>Calendar subscription system implementation</li>
    <li>Admin v2 dashboard and authentication</li>
    <li>Feature flags system architecture</li>
    <li>Security considerations and deployment guide</li>
    <li>API documentation</li>
    </ul>
</li>
<li><strong><a href="./changelogs/v2.2.0.md">v2.2.0 - Robert's Rules Reference System</a></strong> (December 29, 2025)
    <ul>
    <li>Complete feature documentation</li>
    <li>Technical implementation details</li>
    <li>Content coverage and organization</li>
    <li>JavaScript functionality</li>
    <li>Deployment guide</li>
    <li>Browser compatibility</li>
    </ul>
</li>
<li><strong><a href="./changelogs/v2.1.0.md">v2.1.0 - Security & Authentication Enhancements</a></strong> (December 26, 2025)
    <ul>
    <li>Complete feature documentation</li>
    <li>Technical implementation details</li>
    <li>Deployment guide</li>
    <li>Security metrics</li>
    <li>Testing documentation</li>
    </ul>
</li>
</ul>

*Note: Detailed changelogs for v1.0.0 and v2.0.0 were not created as they preceded the structured changelog system.*

---

## Version History Summary

<ul>
<li><strong>v2.5.2</strong> (2026-01-29) - Committee Minutes Editor
    <ul>
    <li>Full committee minutes editor reusing ChapterMinutes infrastructure</li>
    <li>Committee-scoped attendance (members + chairs, deduplicated)</li>
    <li>Any member can create; chairs/admins/designated secretaries can edit</li>
    <li>Publish to committee documents with optional chapter documents publish</li>
    <li>Committee-specific PDF title and subtitle</li>
    <li>Data isolation: chapter views filter committee__isnull=True</li>
    <li>Migration 0072: committee FK on ChapterMinutes, can_take_minutes on CommitteePermissions</li>
    </ul>
</li>
<li><strong>v2.5.1</strong> (2026-01-28) - Minutes Editor Bug Fixes & UX Improvements ⏳
    <ul>
    <li>Fixed delete minutes permission error (is_superuser → is_admin)</li>
    <li>Published minutes PDF now regenerates on edit</li>
    <li>Text file display support in document viewer</li>
    <li>Fixed marker deletion breaking adjacent sections</li>
    <li>Extended file upload validation for text/data file types</li>
    <li>Section headers, enders, and boxed PDF sections</li>
    <li>Adjourn button, end time field, edit tracking</li>
    </ul>
</li>
<li><strong>v2.5.0</strong> (2026-01-28) - Chapter Minutes, Announcements & Document Viewer ⏳
    <ul>
    <li>Chapter Minutes system with inline motions, votes, attendance, PDF export</li>
    <li>Announcements system with scheduling, targeting, email tracking</li>
    <li>Unified document viewer (PDF, DOCX, images, text files)</li>
    <li>Officer dashboard: event management, resolutions, reports, activity logs</li>
    <li>IP address encryption for login history</li>
    <li>Committee voting timer (voting_ends_at)</li>
    </ul>
</li>
<li><strong>v2.4.1</strong> (2026-01-22) - Minor UI Improvement Updates ✅
    <ul>
    <li>Dark mode fixes for Constitution & Bylaws pages</li>
    </ul>
</li>
<li><strong>v2.4.0</strong> (2026-01-19) - Bug Report System & Email Service Update ✅
    <ul>
    <li>Comprehensive bug reporting system with user submissions</li>
    <li>Public bug tracker with filtering and status tracking</li>
    <li>Admin-only bug management dashboard (user_id 73)</li>
    <li>Brevo API email integration (replaces blocked SMTP)</li>
    <li>Fixed IP address handling for X-Forwarded-For headers</li>
    <li>Fixed WSGI/ASGI settings module configuration</li>
    </ul>
</li>
<li><strong>v2.3.0</strong> (2026-01-06) - Calendar Subscriptions & Admin v2 System ✅
    <ul>
    <li>Auto-updating calendar subscription feeds with secure tokens</li>
    <li>Admin v2 advanced administration dashboard</li>
    <li>Site-wide feature flags system with granular control</li>
    <li>Page toggles to enable/disable specific pages</li>
    <li>Chat system feature flag protection</li>
    <li>Enhanced security and comprehensive audit logging</li>
    </ul>
</li>
<li><strong>v2.2.0</strong> (2025-12-29) - Reference Documentation System ✅
    <ul>
    <li>Robert's Rules of Order reference page (10 comprehensive sections)</li>
    <li>Constitution & Bylaws reference page (complete governing documents)</li>
    <li>Live search with text highlighting on both pages</li>
    <li>Table of contents with active section tracking</li>
    <li>Cross-references linking related content between documents</li>
    <li>Quick reference tables for motions, voting, and GPA levels</li>
    <li>Responsive design for all devices</li>
    </ul>
</li>
<li><strong>v2.1.1</strong> (2025-12-26) - Changelog Organization ✅
    <ul>
    <li>Created changelogs archive folder</li>
    <li>Reorganized documentation structure</li>
    <li>Improved version tracking</li>
    </ul>
</li>
<li><strong>v2.1.0</strong> (2025-12-26) - Security & Authentication Enhancements ⏳
    <ul>
    <li>Password reset system with email verification</li>
    <li>Login rate limiting and brute force protection</li>
    <li>Admin panel access monitoring</li>
    <li>Enhanced audit logging</li>
    <li>Email management for users</li>
    </ul>
</li>
<li><strong>v2.0.0</strong> (2025-12-22) - Critical Security Overhaul & Production Deployment 🚀
    <ul>
    <li><strong>First production deployment to https://am-parliament.org</strong></li>
    <li>Password-based authentication (replaced user_id login)</li>
    <li>MIME type file validation</li>
    <li>Password complexity requirements</li>
    <li>Session and cookie security</li>
    <li>HTTPS/SSL configuration</li>
    <li>Admin impersonation logging</li>
    </ul>
</li>
<li><strong>v1.0.0</strong> (2025-09-XX) - Initial Release (Development Only)
    <ul>
    <li>Basic Parliament functionality</li>
    <li>Insecure authentication (username + user_id)</li>
    <li>Limited security measures</li>
    <li>Foundation for future improvements</li>
    <li><strong>Never deployed to production</strong></li>
    </ul>
</li>
</ul>

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

**Last Updated:** 2026-01-29
**Next Review:** 2026-02-28
