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


### v2.10.0 - Songbook Lyrics & Pledge Initiation Fixes (04-03-2026)
Songbook feature enhancements with complete lyrics for all songs, plus critical fixes for pledge initiation.

**Deployment Status:** Pending

**Type:** Feature Enhancement & Bug Fix

**Songbook Enhancements:**

- **Complete Song Lyrics**: Clean, properly formatted lyrics for 40 songs extracted from "Beta Theta Pi Song Book Revised 2005" (Beta Tunes)
- **Lyrics Update Command**: Management command `update_song_lyrics` with options:
  - `--dry-run` - Preview changes without saving
  - `--force` - Overwrite existing lyrics
- **Title Alias Support**: Handles database titles that don't exactly match songbook (e.g., "As Beta Now We Meet" → "As Betas Now We Meet")
- **Songs Updated**: The Alumni's Return, As Betas Now We Meet, The Banquet Hall, Beta Day, Beta Doxology, Beta Hymn, Beta Lullaby, The Beta Marseillaise, Beta Praise, Beta Rose, The Beta Shrine, The Beta Stars, Beta Sweetheart, Beta's Emblems, The Crow Song, For The Staunchest, Gemma Nostra, I Took My Girl Out Walking, The Jolly Greeks, The Loving Cup, Marching Along, My Beta Girl, Parting Song, She Wears My Beta Pin, The Sons of the Dragon, There's a Scene, Ti-de-i-de-o, To the Pledge, We Gather Again, Wooglin Forever!, Wooglin to the Pledge, and more
- **4 Songs Unavailable**: Good Betas Sing Forever, Ring the Bells of Old Miami, We'll Always Hang Together, I Love You (Only You) Beta Girl - not included in 2005 songbook edition

**Bug Fixes:**

- **Pledge Initiation Data Loss**: Fixed critical bug where service hours, attendance, and other data was being CASCADE deleted during pledge initiation
  - Added transaction safety with `transaction.atomic()` to ensure all-or-nothing operations
  - Added verification step before deletion to check for remaining FK references
  - Logs all FK updates for audit trail

- **Pledge Initiation FK Constraint Errors**: Fixed critical bug preventing pledge initiation due to foreign key constraint violations
  - Changed initiation approach from DELETE to INSERT/UPDATE/DELETE to properly handle FK constraints
  - Handles unique constraints (username, email) during user_id migration
  - Preserves all historical data (Kai reports, attendance, service hours, etc.) when initiating pledges

- **Fixed Incorrect FK Column Names**: Corrected 4 wrong column names in related_tables:
  - `src_servicememberexpectation`: `user_id` → `member_id`
  - `src_servicehourssubmission`: `user_id` → `submitted_by_id`
  - `src_servicehoursadjustment`: `user_id` → `member_id`, `created_by_id` → `adjusted_by_id`
  - `src_announcementemaillog`: `sent_by_id` → `initiated_by_id`

- **Added 12 Missing FK References**: Added missing foreign key references that could cause initiation failures:
  - `src_serviceperiod.created_by_id`
  - `src_serviceformfield.created_by_id`
  - `src_slatingapplication.reviewer_id`
  - `src_slatinginterview.destroyed_by_id`
  - `src_slatingperiod.created_by_id`, `admin_transferred_from_id`
  - `src_slate.created_by_id`, `approved_by_id`
  - `src_kaiformfield.created_by_id`
  - `src_notificationschedule.created_by_id`
  - `src_committee.admin_id`

**Files Changed:**

- `src/management/commands/update_song_lyrics.py` - Complete rewrite with clean lyrics from 2005 songbook
- `src/view/officer/manage_members.py` - Fixed initiation logic and FK references

---

### v2.9.0 - Security Update (04-01-2026)
Major security update with login rate limiting, attack detection, and session management.

**Deployment Status:** Pending

**Type:** Security Update

**Security Features:**

- **Login Rate Limiting**: 5 failed attempts = 15-minute lockout (prevents brute force attacks)
- **SQL Injection & XSS Protection**: `InputSanitizationMiddleware` detects and blocks attack patterns
- **Auto IP Blocking**: 10+ attacks in 1 hour = automatic 1-hour IP block
- **Security Headers**: X-Content-Type-Options, X-Frame-Options, X-XSS-Protection, Referrer-Policy
- **Session Viewer**: Users can view all active sessions and log out remotely
- **Device Detection**: Track browser, OS, and device type for each session

**New Features:**

- Active Sessions page (`/account/sessions/`)
- Remote session logout (individual or bulk)
- Security & Privacy section in Preferences
- Automatic session tracking on login

**Files Changed:**

- `src/view/login_view.py` - Login rate limiting
- `src/middleware/security.py` - Attack detection middleware
- `Parliament/settings_postgres.py` - Registered security middleware
- `src/models.py` - Added `UserSession` model
- `src/view/session_viewer.py` - Session management views (new)
- `templates/account/sessions.html` - Session viewer UI (new)
- `templates/preferences.html` - Added Security & Privacy section

**[View Detailed Changelog](changelogs/v2.9.0.md)**

---

### v2.8.6 - Mobile UI Fixes & Performance Optimizations (03-30-2026)
Mobile UI improvements, email warmup system, password requirement fixes, and database optimizations.

**Deployment Status:** Deployed

**Key Features:**

- **Email Warmup System**: Pre-loads announcement emails while user reviews confirmation page for faster sending
- **Warmup Logs in Admin-v2**: Email logs page now shows warmup and cancelled statuses with proper badges

**Bug Fixes:**

- **Email Warmup Foreign Key Error**: Fixed race condition where stale cache data referenced deleted email logs
- **Documents Page Mobile UI**: Fixed layout issues with document items, folder headers, vote results, and committee headers on mobile devices
- **DOCX Hyperlink Overflow**: Fixed hyperlinks in DOCX files going off-screen on mobile with word-break CSS
- **Password Requirement Text**: Fixed inconsistent "8 characters" text to show correct "9 characters" requirement across all pages (password reset, admin security)

**Performance Optimizations:**

- **Removed Redundant Notifications**: Announcements and events no longer create per-user notification rows (saves ~32 rows per announcement/event)
- **Email Warmup**: Pre-renders email templates and pre-creates recipient records during confirmation page load

**Files Changed:**

- `templates/chapter_documents.html` - Mobile responsive fixes
- `templates/chapter_documents_item.html` - Mobile responsive fixes
- `templates/view_document.html` - DOCX hyperlink overflow fix
- `templates/registration/password_reset_confirm.html` - Password requirement text
- `templates/admin_v2/user_login_security.html` - Password requirement text
- `src/view/admin_v2.py` - Password validation (8 → 9 characters)
- `src/view/officer/manage_announcements.py` - Email warmup system
- `src/view/officer/manage_events.py` - Removed redundant notifications
- `src/notifications.py` - Removed redundant notifications
- `templates/officer/confirm_announcement_email.html` - Warmup JavaScript

**[View Detailed Changelog](changelogs/v2.8.6.md)**

---

### v2.8.5 - Two-Factor Authentication System (03-26-2026)
Complete 2FA implementation with TOTP support, admin dashboard, and comprehensive test coverage.

**Deployment Status:** Pending

**Key Features:**

- **2FA Setup Flow**: QR code scanning with Google Authenticator, Authy, or Microsoft Authenticator
- **Policy-Based Enforcement**: Configure 2FA requirements by role (none, admins, officers, all members, custom)
- **Individual Overrides**: Mark specific users as required or exempt regardless of policy
- **Admin Dashboard**: New `/admin-v2/two-factor/` page for managing 2FA settings and user requirements
- **Dismiss for 1 Hour**: Users can postpone 2FA setup temporarily instead of constant redirects
- **Dark Mode Support**: All 2FA pages fully support dark mode

**Bug Fixes:**

- **Verification Loop Fix**: Fixed issue where users were stuck on verify page after entering correct code
- **Session Handling**: Fixed middleware session access for compatibility with tests

**Database Changes:**

- Added `TwoFactorRequirement` model for per-user 2FA settings
- Added `2fa_policy_mode` site setting

**Dependencies:**

- Added `django-otp==1.5.4` for TOTP authentication
- Added `qrcode==7.4.2` for QR code generation

**Test Coverage:**

- 52 comprehensive tests covering setup, verification, policies, and admin dashboard

**[View Detailed Changelog](changelogs/v2.8.5.md)**

---

### v2.8.2 - Admin Management Tools & Bug Fixes (03-18-2026)
New admin management pages for roles and committees, officer admin sync functionality, and bug fixes.

**Deployment Status:** Pending

**Key Features:**

- **Officer Admin Sync**: New tool to sync admin privileges based on officer roles (President, EVP, VPB grant admin by default)
- **Manage Roles Page**: Admin-only page to add, edit, and delete officer roles with `grants_admin` configuration
- **Manage Committees Page**: Full CRUD for committees including edit, delete, and ad-hoc/permanent status
- **Protected Admin Account**: User ID 73 protected from having admin removed during sync

**Bug Fixes:**

- **Service Hours Form Duplicate**: Fixed form showing fields twice by excluding built-in fields from custom fields query

**Database Changes:**

- Added `grants_admin` field to Role model
- Added `is_ad_hoc` field to Committee model

**[View Detailed Changelog](changelogs/v2.8.2.md)**

---

### v2.8.1 - Mobile UI Fixes & VPP Access Fix (03-16-2026)
Bug fixes for mobile layout issues and VPP role access control.

**Deployment Status:** Deployed

**Bug Fixes:**

- **Home Page Announcement URLs**: Long URLs in announcements now wrap properly instead of overflowing off the page on mobile
- **Committee Page Button Labels**: "All Committees" and "Admin: View All" buttons now show readable text on mobile ("Browse" and "View All") instead of just "All"
- **Committee Dropdown Positioning**: Dropdown menu now centers on mobile screens instead of being cut off on the left side
- **VPP Role Check Case-Insensitive**: VPP role check now uses `code__iexact='VPP'` to match regardless of case (VPP, vpp, Vpp, etc.)
- **VPP Decorator Improvements**: Added `functools.wraps` for proper function metadata preservation; DEBUG mode bypass for dev testing only

**[View Detailed Changelog](changelogs/v2.8.1.md)**

---

### v2.8.0 - Service Hours System & Directory Export (03-16-2026)
Comprehensive service hours tracking system for members and VPP officers, plus directory export functionality and mobile UI improvements.

**Deployment Status:** Ready for Deployment

**Key Features:**

- **Service Hours Dashboard**: Member-facing dashboard showing progress toward service hour requirements with submission history
- **Service Hours Submission**: Members can submit service hours with date, organization, description, hours, and optional proof/documentation
- **Service Periods**: VPP can create and manage service periods with configurable hour requirements and approval settings
- **Member Expectations**: Set default hours per period with individual member overrides (exemptions, increased requirements)
- **Submission Management**: VPP can approve, reject, or request changes on submissions with reviewer notes
- **Service Form Builder**: Customize the submission form with additional fields (like Kai form builder)
- **Built-in Fields Display**: Form builder shows all fields including built-in ones with edit capability
- **Directory Export**: Export member directory to CSV, TXT, or Excel (.xlsx) formats with names, emails, phones, and roles
- **Mobile Footer Fix**: Footer links now wrap properly on mobile without awkward pipe separator stacking

**Database Changes:**

- Added `ServicePeriod` model for service hour periods
- Added `ServiceMemberExpectation` model for individual member hour requirements
- Added `ServiceHoursSubmission` model for hour submissions with approval workflow
- Added `ServiceFormField` model for custom form fields (with `is_builtin` flag)
- Added `ServiceFieldResponse` model for custom field responses
- Added `ServiceActivity` model for audit logging

**New Files:**

- `src/view/service_user_dashboard.py` - Member service hours views
- `src/view/service_hours.py` - VPP officer management views
- `src/view/service_form_builder.py` - Form customization views
- `templates/service_hours/` - 10 template files for member and officer interfaces

**Access Control:**

- Added `vpp_required` decorator restricting officer pages to VPP role holders and admins
- DEBUG mode bypass for development testing

**Dependencies:**

- Added `openpyxl==3.1.2` for Excel export functionality

**[View Detailed Changelog](changelogs/v2.8.0.md)**

---

### v2.8.0 - Officer Guide System, Notifications Dashboard & Expanded Guides (03-02-2026)
Comprehensive documentation system with 17 in-app guides covering officer and member features, plus a centralized notifications dashboard for scheduling and monitoring.

**Deployment Status:** Ready for Deployment

**Key Features:**

- **Officer Guide System**: Complete guide hub at `/guide/` with static documentation pages
- **10 Officer Guides**: Events, Announcements, Attendance, Chapter Minutes, Managing Members, Slating, Kai Reports, Resolutions, Activity Logs, Kai Form Customization
- **7 Member Guides**: Profile Management, Calendar & Subscription, Notifications, Submitting Excuses, Two-Factor Authentication, Member Directory, Global Search
- **Interactive Tour Framework**: Model support for step-by-step walkthrough tours (GuideTour, GuideTourStep, UserTourProgress)
- **Notifications Dashboard**: Admin v2 notification management at `/admin-v2/notifications/`
- **Notification Scheduling**: Create scheduled notification reminders (event, vote, attendance, dues)
- **Notification Logs**: View delivery history with filtering and detailed log views

**Kai Bug Fixes:**

- Fixed closure request visibility - now shows when outcome is set (not just when archived)
- Added "Drop Case" option for submitters to withdraw complaints
- Added accused user view for viewing reports and requesting closure
- Fixed status display issues in user_view_report.html

**Database Changes:**

- Added `GuideTour`, `GuideTourStep`, `UserTourProgress` models for interactive tours
- Added `GuideArticle` model for dynamic guide content
- Added `NotificationSchedule` model for automated notification triggers
- Added `NotificationLog` model for delivery tracking and analytics
- Extended `KaiClosureRequest` with request_type choices (closure, drop, accused_closure)

**New Files:**

- `src/view/guide.py` - Guide system views (17 guide pages)
- `src/view/notification_admin.py` - Notification dashboard views
- `templates/guide/` - 17 guide templates across officers/ and members/ directories
- `templates/admin_v2/notifications/` - Dashboard, schedules, and logs templates

---

### v2.7.2 - Chat & Bug Report Improvements (03-03-2026)
Chat system improvements and bug report email fix.

**Deployment Status:** Ready for Deployment

**Key Features:**

- **Chat Timestamps Include Date**: Messages now display "Mar 3, 4:30 PM" instead of just "4:30 PM"
- **Chat Feature Flag with Polling Control**: The `chats` feature flag now properly controls all chat functionality including JavaScript polling. When disabled, polling stops gracefully.

**Bug Fixes:**

- Fixed bug report email notifications not sending when using Brevo (Anymail) - was incorrectly checking for `EMAIL_HOST_USER` instead of `BREVO_API_KEY`

**[View Detailed Changelog](./changelogs/v2.7.2.md)**

---

### v2.7.1 - Announcement Email Improvements (03-02-2026)
Feature enhancements and bug fixes for the announcement email system.

**Deployment Status:** Ready for Deployment

**Key Features:**

- **Email Logs Console Output**: Detailed step-by-step logging showing exactly what happens during email sends
- **Scheduled Announcement Emails**: Announcements with future `publish_at` dates can now have emails sent automatically via cron job (every 5 minutes)
- **Admin-v2 Manual Email Trigger**: Pending scheduled emails shown in Admin v2 → Email Logs with "Send Now" button
- **Race Condition Protection**: Database row locking prevents duplicate emails when cron jobs overlap or admin triggers manually
- **Clickable Home Page Cards**: Statistics cards (Members, Votes, Events, Committees) now link to their respective pages *(suggested by Calin Cox)*
- **Email Confirmation UX**: Improved confirmation page with prominent Send button, smaller Skip link, and confirmation popup

**Bug Fixes:**

- Fixed system logs page (`/admin/view-logs/`) being completely blank due to log parsing format mismatch
- Fixed email logs not appearing for new announcements by restructuring error handling with try/finally
- Fixed announcement content cutoff on manage page - now shows "Show more/less" toggle for long content

**Database Changes:**

- Added `console_log` field to `AnnouncementEmailLog` model
- Added `send_email_on_publish` and `email_sent_at` fields to `Announcement` model

**New Files:**

- `src/management/commands/process_scheduled_announcements.py` - Cron-based scheduled email processing with race condition protection

**[View Detailed Changelog](./changelogs/v2.7.1.md)**

---

### v2.6.4 - Kai Dashboard & Plurality Voting Enhancements (02-28-2026)
Major feature release introducing a user-facing Kai report dashboard with closure request workflow, and enhanced plurality voting with multi-select and runoff capabilities.

**Deployment Status:** Ready for Deployment

**Key Features:**

- **User Kai Dashboard**: Personal dashboard at `/kai/` showing all submitted reports with ability to view details and request closure
- **Closure Request Workflow**: Users can request case closure; Kai chairs approve or deny with email notifications
- **Kai Form Builder**: Chairs can customize the report form with additional fields (text, select, file upload, etc.)
- **Multi-Select Plurality Voting**: Allow voters to select 1-10 options in plurality votes (chapter and committee)
- **Runoff Voting**: Create runoff votes from top N options of close/tied plurality results
- **Custom Field Responses**: View custom field data in manage report view

**Database Changes:**

- Added `KaiFormField` model for dynamic form field definitions
- Added `KaiReportFieldResponse` model for custom field responses
- Added `KaiClosureRequest` model for closure request workflow
- Added plurality voting fields to `Legislation` and `CommitteeLegislation` models

**[View Detailed Changelog](./changelogs/v2.6.4.md)**

---

### v2.6.3 - Memory Optimization & Server Stability (02-23-2026)
Comprehensive memory leak investigation and fixes to address steadily increasing RAM usage over time.

**Deployment Status:** Ready for Deployment

**Key Fixes:**

- 🧠 **PerformanceMiddleware**: Reduced stored requests from 1000 to 100 per worker
- ⚡ **Feature Flags Caching**: Added 60-second caching to eliminate per-request DB queries
- 🔄 **Gunicorn Lifecycle**: Reduced max_requests to 500; added cleanup hooks on worker exit
- 📝 **Log Rotation**: Changed to RotatingFileHandler (10MB max, 3 backups)
- 🗑️ **LocMemCache**: Reduced to 1000 entries with 5-min timeout and cull frequency
- 🧹 **Cleanup Command**: New `cleanup_sessions` command for expired data removal

**New Management Commands:**

- `python manage.py cleanup_sessions` - Clean expired sessions, old logs, notifications
- `python manage.py memory_report` - Comprehensive memory diagnostics
- `shell/server_maintenance.sh` - Automated daily maintenance script

**[📄 View Detailed Changelog](./changelogs/v2.6.3.md)**

---

### v2.6.0 - Officer Member Management & Role Numbers (02-03-2026)
Comprehensive officer-level member management system with in-app controls for adding, editing, and managing members directly from the user list page.

**Deployment Status:** ✅ Deployed

**Key Features:**

- 👥 **In-App Member Management**: Officers can add, edit, and delete members directly from the user list page
- 🎓 **Batch Pledge Initiation**: Select multiple pledges and initiate them as members with role number assignment
- 🔢 **Role Number System**: Track member roll numbers assigned at initiation (permanent chapter identifier)
- 🔔 **Initiation Notifications**: Newly initiated members receive in-app welcome notifications
- 🔒 **Double-Submit Prevention**: All forms now prevent duplicate submissions on slow connections (global protection)
- 🆔 **Auto-Generate Pledge IDs**: When adding pledges, a temporary ID is auto-generated (format: P-XXXXXX)
- 📊 **Performance Monitoring Dashboard**: Admin-v2 now includes real-time server health checks and performance metrics
- ⚡ **Performance Optimizations**: Added preconnect hints and optimized resource loading for faster page loads
- 📱 **Mobile PDF Scrolling Fix**: Fixed issue where PDFs couldn't be scrolled on mobile devices
- 🌙 **Dark Mode Improvements**: Edit announcement page now supports dark mode

**Member Management Features:**

- ➕ **Add Member**: Create new members with auto-generated passwords, set type/status/roles
- ✏️ **Edit Member**: Update name, email, preferred name, member type, status, roles, and roll number
- 🗑️ **Delete Member**: Soft delete (deactivate) or hard delete with confirmation
- 📋 **Role Number Display**: Roll numbers shown in user list table and mobile cards

**Database Changes:**

- Added `role_number` field to `ParliamentUser` model (unique, nullable)
- Migration required for role_number field

**[📄 View Detailed Changelog](./changelogs/v2.6.0.md)**

---

### v2.5.2 - Committee Minutes Editor (01-29-2026)
Extends the Chapter Minutes editor to committees with full editor support, committee-scoped attendance, and flexible permissions.

**Deployment Status:** ✅ Deployed

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

**Deployment Status:** ✅ Deployed

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

**Deployment Status:** ✅ Deployed 

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
<li><strong><a href="changelogs/v2.8.6.md">v2.8.6 - Mobile UI Fixes & Performance Optimizations</a></strong> (March 30, 2026)
    <ul>
    <li>Email warmup system for faster announcement sending</li>
    <li>Fixed documents page mobile UI issues</li>
    <li>Fixed DOCX hyperlink overflow on mobile</li>
    <li>Fixed password requirement text (8 → 9 characters)</li>
    <li>Removed redundant per-user notifications (database optimization)</li>
    </ul>
</li>
<li><strong><a href="changelogs/v2.8.5.md">v2.8.5 - Two-Factor Authentication System</a></strong> (March 26, 2026)
    <ul>
    <li>Complete 2FA implementation with TOTP support</li>
    <li>QR code setup with Google Authenticator, Authy, Microsoft Authenticator</li>
    <li>Policy-based enforcement (none, admins, officers, all members, custom)</li>
    <li>Individual user overrides (required/exempt)</li>
    <li>Admin-v2 dashboard for 2FA management</li>
    <li>Dismiss for 1 hour feature</li>
    <li>Dark mode support on all 2FA pages</li>
    <li>Fixed verification loop bug</li>
    <li>52 comprehensive tests</li>
    </ul>
</li>
<li><strong><a href="changelogs/v2.8.1.md">v2.8.2 - Mobile UI Fixes & VPP Access Fix</a></strong> (March 16, 2026)
    <ul>
    <li>Fixed long URLs overflowing in home page announcements on mobile</li>
    <li>Fixed committee page button labels and dropdown positioning on mobile</li>
    <li>Fixed footer link stacking on mobile</li>
    <li>VPP role check now case-insensitive</li>
    <li>Added functools.wraps to vpp_required decorator</li>
    </ul>
</li>
<li><strong><a href="changelogs/v2.8.0.md">v2.8.1 - Service Hours System & Directory Export</a></strong> (March 16, 2026)
    <ul>
    <li>Complete service hours tracking system for members and VPP officers</li>
    <li>Member dashboard with progress tracking and submission history</li>
    <li>Service periods with configurable requirements and approval workflows</li>
    <li>Form builder for custom fields with built-in field display</li>
    <li>Directory export to CSV, TXT, and Excel formats</li>
    <li>6 new database models, vpp_required decorator</li>
    </ul>
</li>
<li><strong><a href="./changelogs/v2.7.2.md">v2.7.2 - Chat & Bug Report Improvements</a></strong> (March 3, 2026)
    <ul>
    <li>Chat message timestamps now include date</li>
    <li>Chat feature flag with polling control</li>
    <li>Fixed bug report email notifications for Brevo</li>
    </ul>
</li>
<li><strong><a href="./changelogs/v2.7.1.md">v2.7.1 - Announcement Email Improvements</a></strong> (March 2, 2026)
    <ul>
    <li>Email logs console output with detailed step-by-step logging</li>
    <li>Scheduled announcement emails via cron job (every 5 minutes)</li>
    <li>Admin-v2 manual email trigger with "Send Now" button</li>
    <li>Race condition protection using database row locking</li>
    <li>Clickable home page statistics cards</li>
    <li>Email confirmation UX improvements</li>
    <li>Fixed system logs page blank issue</li>
    <li>Fixed email logs not appearing for new announcements</li>
    <li>Fixed announcement content cutoff with expand/collapse toggle</li>
    </ul>
</li>
<li><strong><a href="./changelogs/v2.6.4.md">v2.6.4 - Kai Dashboard & Plurality Voting Enhancements</a></strong> (February 28, 2026)
    <ul>
    <li>User Kai Dashboard with report listing and details view</li>
    <li>Closure request workflow with approval/denial</li>
    <li>Form builder for custom Kai report fields</li>
    <li>Multi-select plurality voting (1-10 options)</li>
    <li>Runoff voting creation from top options</li>
    <li>New models: KaiFormField, KaiReportFieldResponse, KaiClosureRequest</li>
    </ul>
</li>
<li><strong><a href="./changelogs/v2.6.3.md">v2.6.3 - Memory Optimization & Server Stability</a></strong> (February 23, 2026)
    <ul>
    <li>PerformanceMiddleware memory optimization</li>
    <li>Feature flags caching implementation</li>
    <li>Gunicorn worker lifecycle improvements</li>
    <li>Log rotation and cache optimization</li>
    <li>New management commands for cleanup and diagnostics</li>
    </ul>
</li>
<li><strong><a href="./changelogs/v2.6.0.md">v2.6.0 - Officer Member Management & Role Numbers</a></strong> (February 3, 2026)
    <ul>
    <li>In-app member management for officers (add, edit, delete)</li>
    <li>Batch pledge initiation with role number assignment</li>
    <li>Role number tracking and display in user list</li>
    <li>Initiation notifications for new members</li>
    <li>Double-submit prevention on announcement forms</li>
    <li>Dark mode improvements for edit announcement page</li>
    </ul>
</li>
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
<li><strong>v2.8.6</strong> (2026-03-30) - Mobile UI Fixes & Performance Optimizations
    <ul>
    <li>Email warmup system for faster announcement email sending</li>
    <li>Fixed documents page mobile UI (layout, overflow, responsive design)</li>
    <li>Fixed DOCX hyperlink overflow on mobile devices</li>
    <li>Fixed password requirement text (8 → 9 characters) across all pages</li>
    <li>Removed redundant per-user notifications for announcements and events</li>
    </ul>
</li>
<li><strong>v2.8.5</strong> (2026-03-26) - Two-Factor Authentication System
    <ul>
    <li>Complete 2FA implementation with TOTP support (Google Authenticator, Authy, etc.)</li>
    <li>Policy-based enforcement: none, admins_only, officers_and_admins, all_members, custom</li>
    <li>Individual user overrides (required/exempt) with admin dashboard</li>
    <li>Admin-v2 2FA management dashboard at /admin-v2/two-factor/</li>
    <li>Dismiss for 1 hour feature to postpone 2FA setup temporarily</li>
    <li>Dark mode support on all 2FA pages (setup, verify, disable)</li>
    <li>Fixed 2FA verification loop where users couldn't leave verify page</li>
    <li>52 comprehensive tests for all 2FA functionality</li>
    <li>Added django-otp and qrcode dependencies</li>
    </ul>
</li>
<li><strong>v2.8.2</strong> (2026-03-16) - Mobile UI Fixes & VPP Access Fix
    <ul>
    <li>Fixed long URLs overflowing in home page announcements on mobile</li>
    <li>Fixed committee page button labels showing only "All" on mobile</li>
    <li>Fixed committee dropdown menu positioning on mobile</li>
    <li>Fixed VPP role check to be case-insensitive</li>
    <li>Improved vpp_required decorator with functools.wraps</li>
    </ul>
</li>
<li><strong>v2.8.1</strong> (2026-03-16) - Service Hours System & Directory Export
    <ul>
    <li>Complete service hours tracking system for members and VPP officers</li>
    <li>Member dashboard with progress tracking and submission history</li>
    <li>Service periods with configurable requirements and approval workflows</li>
    <li>Individual member hour expectations (overrides/exemptions)</li>
    <li>Form builder for custom fields with built-in field display</li>
    <li>Directory export to CSV, TXT, and Excel formats</li>
    <li>Mobile footer layout fix</li>
    <li>6 new database models, vpp_required decorator</li>
    </ul>
</li>
<li><strong>v2.7.2</strong> (2026-03-03) - Chat & Bug Report Improvements
    <ul>
    <li>Chat message timestamps now include date (e.g., "Mar 3, 4:30 PM")</li>
    <li>Chat feature flag with polling control - disabling stops all polling</li>
    <li>Fixed bug report email notifications for Brevo (Anymail)</li>
    </ul>
</li>
<li><strong>v2.7.1</strong> (2026-03-02) - Announcement Email Improvements
    <ul>
    <li>Email logs console output with detailed step-by-step logging</li>
    <li>Scheduled announcement emails via cron job (every 5 minutes)</li>
    <li>Admin-v2 manual email trigger with "Send Now" button for pending scheduled emails</li>
    <li>Race condition protection using database row locking (prevents duplicate emails)</li>
    <li>Clickable home page statistics cards (Members, Votes, Events, Committees)</li>
    <li>Email confirmation UX: prominent Send, smaller Skip with popup</li>
    <li>Fixed system logs page (/admin/view-logs/) being blank</li>
    <li>Fixed email logs not appearing due to error handling issues</li>
    <li>Fixed announcement content cutoff on manage page with expand/collapse toggle</li>
    <li>New management command: process_scheduled_announcements</li>
    </ul>
</li>
<li><strong>v2.6.4</strong> (2026-02-28) - Kai Dashboard & Plurality Voting Enhancements
    <ul>
    <li>User Kai Dashboard showing submitted reports at /kai/</li>
    <li>Closure request workflow (user requests, Kai chair approves/denies)</li>
    <li>Form builder for Kai chairs to add custom fields</li>
    <li>Multi-select plurality voting (1-10 selections)</li>
    <li>Runoff voting for chapter and committee plurality votes</li>
    <li>Custom field responses displayed in manage report view</li>
    </ul>
</li>
<li><strong>v2.6.3</strong> (2026-02-23) - Memory Optimization & Server Stability
    <ul>
    <li>PerformanceMiddleware optimization (reduced stored requests)</li>
    <li>Feature flags caching (60-second cache)</li>
    <li>Gunicorn lifecycle improvements with cleanup hooks</li>
    <li>Rotating log files and LocMemCache optimization</li>
    <li>New cleanup_sessions and memory_report commands</li>
    </ul>
</li>
<li><strong>v2.6.0</strong> (2026-02-03) - Officer Member Management & Role Numbers ✅
    <ul>
    <li>In-app member management for officers (add, edit, delete members)</li>
    <li>Batch pledge initiation with role number assignment</li>
    <li>Role number tracking and display in user list table</li>
    <li>Initiation notifications for newly initiated members</li>
    <li>Double-submit prevention on announcement forms</li>
    <li>Dark mode support for edit announcement page</li>
    </ul>
</li>
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

**Last Updated:** 2026-03-30
**Next Review:** 2026-04-30
