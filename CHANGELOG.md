# Parliament Development Changelog
This project is licensed under the MIT License. Copyright (c) 2025-2026 Mason Kimball. Source code available at: https://github.com/MasonKimball05/Parliament-New

## Version History Overview

---

### v3.4.0 — AJAX Interactions & Profile UI Cleanup (2026-06-11)

**Type:** UX Enhancement / New Feature

---

#### AJAX Buttons — Profile, Preferences, Admin API Tokens

Forms across three pages now submit via AJAX and give instant in-page feedback, removing full page reloads for every save/add/delete action. The first step of a long-term goal to bring no-reload interactions to most of the site.

**Pattern (reusable):**
- Backend: `is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'` at the top of the POST block. Each branch returns `JsonResponse({'success': True, ...data})` or `JsonResponse({'error': '...'}, status=400)` when AJAX; otherwise falls through to the existing redirect.
- Frontend: `fetch` with `X-Requested-With` + `application/x-www-form-urlencoded`. On success, button briefly shows "Saved!" then resets — no reload. Add forms append new items to the list in-place; delete forms remove the item and re-index the remaining list.

**Profile (`profile_view.py` + `profile.html`):**
- Account settings (username, preferred name, email, phone) — shows "Saved!" or inline error
- Public profile (bio, chapter info, socials, big brother, graduation) — shows "Saved!"
- Change password — shows "Changed!" on success, inline error breakdown from `form.errors.as_json()` on failure, clears form fields on success
- Role history add/delete — appends/removes `li` rows; delete uses DB primary key so no re-indexing needed
- Custom social links add/delete — appends/removes rows; re-indexes hidden `cs_index` inputs after each delete
- Academic items (majors, minors, concentrations) add/delete — same pattern per type; re-indexes `ai_index` inputs
- Initiation chapters add/delete — appends/removes rows; re-indexes `ic_index` inputs
- Profile picture form intentionally left as a full submit (file upload with crop modal)

**Preferences (`preferences.py` + `preferences.html`):**
- Save Preferences button shows "Saving…" → "Saved!" for 2 seconds
- Theme change is the only case that still triggers a page reload — necessary so CSS variables re-apply

**Admin — API Tokens (`api_tokens.html`):**
- Approve, Reject, Revoke buttons now remove the token row from the DOM instead of calling `location.reload()` — table updates instantly
- Edit Scopes now refreshes the scope chips in the row in-place (rebuilds the `<td>` content and updates the button's `data-scopes` attribute) without a reload

**Changed:**
- `src/view/profile_view.py` — Added `from django.http import JsonResponse`; added `is_ajax` detection; all POST branches return JSON when AJAX
- `src/view/preferences.py` — Added `from django.http import JsonResponse`; added `is_ajax` detection; save form returns `{'success': True, 'theme_changed': bool}`
- `templates/profile.html` — Added IDs to all key forms and lists; added `js-*-delete-form` classes and `data-*-index` attributes to all delete forms/list items; added comprehensive AJAX JS block at bottom of page
- `templates/preferences.html` — Added `id="preferences-form"`; added AJAX JS block
- `templates/admin_v2/api_tokens.html` — Replaced `location.reload()` with `removeTokenRow()` for approve/reject/revoke; scopes update patches chips in-place

---

#### Profile Page UI Cleanup

Reorganized the profile page to separate concerns and remove the scattered "sections with buttons in the middle" layout.

- **Account Settings** and **Security** are now separate cards (previously one combined card). Security card contains 2FA, Passkeys, and Change Password accordions.
- **Additional Details** divider added in Public Profile above the per-item accordions (Custom Social Links, Academics, Initiation Chapters, Role History), with a note explaining that each section saves individually.

**Changed:**
- `templates/profile.html` — Closed Account Settings card before Security accordions; opened new Security card with heading; added Additional Details divider with explanatory subtext

---

### v3.3.0 — Authentication UX, Passkey Nudge & API Token Fixes (2026-06-11)

**Type:** New Features / Bug Fixes / Security Enhancement

---

#### Email/Username Login

Users can now sign in using either their username or their registered email address. The login view resolves an email input to the matching username before calling `authenticate()`, so the underlying auth backend is unchanged.

**Changed:**
- `src/view/login_view.py` — Added email-to-username resolution: if the login field contains `@`, looks up the user by `email__iexact` before authenticating. Gracefully falls through on `DoesNotExist` or `MultipleObjectsReturned`.
- `templates/registration/login.html` — Label updated to "Username or Email"; placeholder updated to "Username or email address".

---

#### 2FA Passkey Bypass

Users who have a passkey registered can now use it to complete the 2FA verification step when they log in with a password. A "Use a Passkey instead" button appears on the verify screen (only when the user has passkeys), running the full WebAuthn assertion flow and redirecting on success.

**Changed:**
- `src/view/two_factor.py` — Imports `WebAuthnCredential`; queries it in `two_factor_verify` and passes `has_passkeys` to context.
- `templates/two_factor/verify.html` — Added conditional passkey button and full WebAuthn JS block (begin → credentials.get → complete → redirect), matching the login-page passkey flow.

---

#### Passkey Nudge Modal

Users without a passkey registered now see a modal popup on the Preferences and Profile pages encouraging them to set one up. The modal has no permanent dismiss — it reappears each visit until a passkey is registered, then disappears automatically.

- On **Preferences**: a persistent banner also appears at the top of the page (non-dismissible). The modal's "Set Up Now" link navigates to `profile#passkeys`.
- On **Profile**: the modal's "Set Up Now" button closes the modal and opens + scrolls to the Passkeys accordion inline. Modal is suppressed when viewing another user's profile.

**Changed:**
- `src/view/preferences.py` — `show_passkey_nudge` is now simply `not has_passkeys` (removed `nudge_dismissed` prefs check). Removed `dismiss_passkey_nudge` view and its imports.
- `src/urls.py` — Removed `dismiss_passkey_nudge` URL and import.
- `src/view/profile_view.py` — Added `show_passkey_nudge = (passkey_count == 0) and (user == request.user)` and passes it to context.
- `templates/preferences.html` — Added non-dismissible banner at top; added passkey nudge modal at bottom of page.
- `templates/profile.html` — Added passkey nudge modal; "Set Up Now" opens the passkeys accordion in-place.

---

#### Passkeys Accordion Deep-Link

The Passkeys section on the Profile page can now be opened and scrolled to directly via URL: `profile#passkeys` or `profile?open=passkeys`. Used by the Preferences banner and 2FA screen links.

**Changed:**
- `templates/profile.html` — Added `id="passkeys-accordion"` to the passkeys `<details>` element. Added JS that opens and smooth-scrolls to the accordion when the URL hash or query param matches.

---

#### Set-Email Modal Snooze

The "Set Email" modal in the base layout now respects a 15-minute snooze when the user clicks "Skip for Now". The modal will not reappear until the snooze expires, preventing it from interrupting every page visit during a session.

**Changed:**
- `templates/base.html` — Added `localStorage`-based snooze (`parliament_email_modal_snoozed_until`). On load, checks if within snooze window and hides immediately. "Skip for Now" button sets a 15-minute snooze timestamp on click.

---

#### API Token Admin Button Fix

Fixed a persistent HTTP 400 error on all admin API token action buttons (Approve, Reject, Revoke, Toggle). Root causes were two independent issues that both had to be resolved together.

**Root cause 1:** `postJSON` in `api_tokens.html` was sending `multipart/form-data` via `FormData`. Daphne had issues parsing empty multipart bodies on some requests, causing 400s. Fixed by switching to `application/x-www-form-urlencoded` via `URLSearchParams`.

**Root cause 2:** `CSRF_TRUSTED_ORIGINS` was empty, so Django fell back to Referer-header CSRF validation. Cloudflare was modifying the Referer header on HTTPS requests, causing CSRF checks to fail. Fixed by always including `SITE_URL` in `CSRF_TRUSTED_ORIGINS`.

**Changed:**
- `templates/admin_v2/api_tokens.html` — `postJSON` now sends `application/x-www-form-urlencoded`.
- `Parliament/settings_postgres.py` — Added `CSRF_TRUSTED_ORIGINS` always including `$SITE_URL`.

---

#### API Token UX Fixes

Three UX issues on the API token pages, all discovered after the 400 fix confirmed buttons were working.

**Toggle knob not moving:** `classList.replace()` silently fails if the source class isn't present. Replaced with explicit `remove()` + `add()` calls throughout the toggle JS. Also fixed `dark:bg-gray-600` not being handled (colon in class name breaks `replace()`).

**Revoke/Withdraw navigating to raw JSON:** Both forms were plain HTML submissions with only a JS confirm dialog. Fixed by intercepting `submit`, posting via `fetch`, and reloading the page on success.

**Token request showing error then pending:** The request form was still using `FormData` multipart. Fixed by switching to `URLSearchParams` + explicit `Content-Type` + `credentials: 'same-origin'`. Improved catch message to note the request may have already been submitted.

**Changed:**
- `templates/admin_v2/api_tokens.html` — Toggle button JS uses `remove`/`add` instead of `replace`.
- `templates/preferences.html` — Revoke and withdraw forms use AJAX. Token request form switched to URL-encoded.

---

#### API Token Expiry Bug Fix & `_parse_expiry` Helper

Admin approval was unconditionally overwriting the token's `expires_at`, so approving without setting a date would silently wipe the user-requested expiry. Fixed so the expiry is only updated when the admin explicitly provides a date. Also extracted the repeated 13-line expiry parsing block into a shared `_parse_expiry()` helper.

The approve modal now prefills the expiry date field from the token's existing `expires_at` so the admin can see the user's requested expiry at a glance.

**Changed:**
- `src/view/api.py` — Added `_parse_expiry()` helper above `request_api_token`; replaced duplicated expiry blocks in both views. `admin_approve_token` only writes `expires_at` when the admin provided a value.
- `templates/admin_v2/api_tokens.html` — Approve button carries `data-token-expires` attribute; modal JS prefills the date input from it.

---

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

> **Note on v2.1–v2.23:** These versions were built and deployed between January and May 2026. Changelog entries were not written at the time. Key milestones included: 2FA enforcement, slating system, KAI reporting, push notifications, service hours, house map, big/little family trees, extended member profiles, and the admin-v2 dashboard. Bug fixes from this period are not individually documented.

---

### v2.25.0 - Feature Flags, Announcement Enhancements, Profile Redesign & models.py Split (2026-05-30)

A large session covering five independent areas: feature flag completion, two announcement features (document linking and polls), a profile page UI redesign, and a major code organization overhaul splitting the monolithic models file.

---

#### Feature Flag Audit & Chat Disable

Completes the feature flag system so the `chats` flag actually removes all chat UI across the site when disabled, and fixes a broken seed command.

**Changed:**
- `base.html` nav — Chats link now hidden when the `chats` feature flag is off (previously only checked user preference, not the flag)
- `committee_home.html`, `committee_index.html`, `detail.html` — all chat buttons wrapped in `{% if feature_flags.chats %}`
- `seed_feature_flags.py` — Fixed `page_name` key → `url_name` throughout page toggles section (`PageToggle.url_name` is the actual field name)
- `seed_feature_flags.py` — Added missing `announcements` feature flag (view used `@require_feature_flag('announcements')` but the flag was never seeded)
- `seed_feature_flags.py` — Added clarifying note to `committee_chat` flag description

**Audit findings (no code change):** `calendar_subscriptions`, `global_search`, `kai_reports`, `attendance_tracking` flags are seeded but not enforced in views — noted for future work.

**Files changed:** `templates/base.html`, `templates/committee/committee_home.html`, `templates/committee/committee_index.html`, `templates/committee/detail.html`, `src/management/commands/seed_feature_flags.py`

---

#### Announcement Document Linking (migration 0181)

Allows officers to attach chapter documents to announcements, displayed as linked file pills for members.

**New:**
- `Announcement.linked_documents` — ManyToMany to `CommitteeDocument`; only chapter-published documents are available, enforced server-side
- Document picker in create/edit forms — collapsible panel with checkboxes; badge shows attached count
- Linked document pills on the announcements page below announcement content, each linking to the document download

**Files changed:** `src/models/announcements.py`, `src/migrations/0181_announcement_linked_documents.py`, `src/view/officer/manage_announcements.py`, `templates/officer/create_announcement.html`, `templates/officer/edit_announcement.html`, `templates/announcements.html`

---

#### Announcement Polls & Surveys (migration 0182)

Full poll/survey system attachable to any announcement — officer-managed, with anonymous option, per-question response tracking, results dashboard, and CSV export.

**New Models:**
- `AnnouncementPoll` — one-to-one with `Announcement`; `is_anonymous`, `is_open`, `closes_at`
- `AnnouncementPollQuestion` — text, single-choice, or multiple-choice; ordered
- `AnnouncementPollOption` — selectable choices for single/multiple questions
- `AnnouncementPollResponse` — one per user per poll; tracks who has responded
- `AnnouncementPollAnswer` — per-question answer with M2M options or text field

**New Views:**
- `create_or_edit_poll` (`/officers/announcements/<id>/poll/`) — dynamic question/option builder; delete-poll action
- `poll_results` (`/officers/announcements/<id>/poll/results/`) — aggregate bar charts per question, individual response table (non-anonymous), "who hasn't responded" pill list, CSV export
- `take_poll` (`/announcements/<id>/poll/`) — member-facing poll form; shows confirmation if already responded or poll is closed
- `poll_confirmation` — post-submit thank-you page with anonymous notice if applicable

**Changed:**
- `announcements.html` — "Take Poll" button, "Poll completed" badge, or "Poll closed" badge per announcement; `responded_poll_ids` precomputed in view
- `manage_announcements.html` — "Poll Results" (purple) or "+ Add Poll" (outline) per announcement

**Files changed:** `src/models/announcements.py`, `src/migrations/0182_announcement_poll.py`, `src/view/officer/announcement_polls.py` (new), `src/urls.py`, `src/view/announcements.py`, `templates/officer/announcement_poll_edit.html` (new), `templates/officer/announcement_poll_results.html` (new), `templates/announcement_poll.html` (new), `templates/announcement_poll_confirmation.html` (new), `templates/announcements.html`, `templates/officer/manage_announcements.html`

---

#### Profile Page Redesign

Reduces visual clutter on the profile page by collapsing infrequently-edited sections into accordions, merging the read-only info card into the header, and removing the notification preferences duplicate (already fully covered by Preferences).

**Changed:**
- Header now shows user ID, member type, and status as chips alongside the profile picture; "Preferences & Settings" link added inline below the name
- "Profile Information" read-only card removed — all four fields are now shown in the header
- "Notification Preferences" card removed — fully duplicated the In-App Notifications section on the Preferences page; replaced with a single "Manage Preferences →" row at the bottom of the page
- Account Settings: Two-Factor Authentication and Change Password sections wrapped in collapsible `<details>` accordions; 2FA summary shows "Enabled"/"Not set up" badge without opening
- Public Profile: About Me stays open; Chapter Info, Social Handles, Custom Social Links, Academics, Initiation Chapters, and Role History are all collapsible accordions; count badges shown for Custom Links and Init Chapters when populated

**Files changed:** `templates/profile.html`

---

#### models.py Split into Sub-modules

Breaks the 6280-line monolithic `src/models.py` into 16 focused sub-modules under `src/models/`. No model names, fields, or database tables changed — pure file reorganization. All 216 existing `from src.models import X` import sites work unchanged via the re-export shim in `src/models/__init__.py`.

**New structure (`src/models/`):**

| File | Contents |
|------|----------|
| `users.py` | `ParliamentUser`, `Role`, `RoleHistory`, `UserPreferences`, `TwoFactorRequirement`, `UserSession`, managers, signals |
| `legislation.py` | `Legislation`, `Vote` |
| `committees.py` | `Committee`, `CommitteePermissions`, `CommitteeLegislation`, `CommitteeVote` |
| `documents.py` | `CommitteeMinutes`, `ChapterFolder`, `DocumentTag`, `CommitteeDocument`, `DocumentVersion`, `ChapterMinutes`, `MinutesSection`, `MinutesMotion` |
| `announcements.py` | `Announcement`, `UserAnnouncementView`, poll models (5), `AnnouncementEmailLog`, `AnnouncementEmailRecipient` |
| `events.py` | `Event`, `Attendance`, `AttendanceExcuse` |
| `chat.py` | `ChatChannel`, `ChatChannelPermission`, `ChatMessage`, `ChatReadReceipt` |
| `kai.py` | `KaiReport`, `KaiReportActivity`, `KaiReportTemplate`, `KaiFormField`, `KaiReportFieldResponse`, `KaiClosureRequest` |
| `slating.py` | 12 slating classes (`SlatingPeriod` → `SlatingActivity`) |
| `service.py` | 7 service hours classes |
| `security.py` | `LoginHistory`, `LoginAlert`, `UserWatchFlag`, `IPWhitelist`, `IPBlacklist`, `BugReport`, `QuarantinedAccount`, `HoneypotAccess`, `SystemLockdown`, `SecurityNotificationLog`, `LoginLockout` |
| `notifications.py` | `Notification`, `NotificationSchedule`, `NotificationLog`, `PushSubscription` |
| `activity.py` | `ActivityLog`, generic `post_save`/`post_delete` logging signal receivers |
| `guide.py` | `GuideTour`, `GuideTourStep`, `UserTourProgress`, `GuideArticle` |
| `songs.py` | `SongCategory`, `Song` |
| `landing.py` | `PassedResolution`, `ResolutionSectionImpact`, landing page content models, `ContactSubmission` |

`__init__.py` re-exports all 90+ names (including `_default_user_prefs` and `validate_*` functions referenced by migrations) so zero import sites required updating.

**Files changed:** `src/models.py` → `src/models/__init__.py` + 16 sub-modules

---

### v2.30.0 - Chat Overhaul, UI Refresh & Guest Permissions Overhaul (2026-06-02 → 2026-06-03)

Push notifications fire when a message is sent and you're not actively viewing the channel. New per-channel notification preference (All / @Mentions Only / None) stored in `ChatNotificationPreference` model. @mentions with autocomplete, highlighted in rendered messages, used to route push notifications. Red unread badge on Chats nav link. URLs autolink. Newlines render. "Load older messages" for history. PWA: Parliament can now be installed as a standalone desktop/mobile app via any modern browser — install prompt appears in Chrome/Edge address bar. Run migration 0185 and purge Cloudflare cache on deploy.

**Chat UI refresh (2026-06-02):** Circular avatars, own-message left-border accent (replaced pink background), auto-growing textarea input with Enter-to-send, smaller poll status dot, pill-style load-older button, channel icon chip in header, SVG back button.

**Admin & Officer base templates (2026-06-02):** Two new dedicated base templates — `admin_v2/base.html` (dark charcoal, red "Admin v2" label) and a rewritten `admin_base.html` (gray-800 officer toolbar with links to all officer tools). Added `{% block subnav %}` hook to `base.html` so both bases render a toolbar below the main nav without touching it. 58 templates migrated to use the correct base. `can_access_kai` property added to `ParliamentUser` — Kai link in officer toolbar gated on actual Kai chair role, not just `is_admin`.

**Bug fixes (2026-06-03):** Admin v2 dashboard card double-click bug — `DOMContentLoaded` was restoring visual state from localStorage but not syncing `data-expanded`, so `toggleCard()` always read the stale HTML default on first click. Fixed by writing the resolved state back to `card.dataset.expanded`. Chat message hover highlight invisible in dark mode — `dark:hover:bg-gray-700/60` was not in the compiled Tailwind CSS; replaced with `/50` variant which is. Chat settings gear button added to custom (non-committee) channel header for admins.

**Committee chat guest permissions overhaul (2026-06-03):**
- Alumni users now appear in the guest list (previously filtered to Active only)
- Bulk add: select multiple users with a shared permission level, submit all at once
- Bulk remove: row checkboxes + master select-all; "Remove Selected" button appears when any row is checked
- Inline permission update bug fixed: revert logic now correctly restores only the changed checkbox rather than blindly setting it to `true`, preventing a `can_read=False, can_write=True` inconsistent state
- `update_or_create` used in add endpoint — adding an already-existing guest updates their permissions rather than erroring
- Available users list split into Active / Alumni optgroups; already-added users shown as disabled with a label
- Toast notifications replace `alert()` throughout
- Bulk endpoints: `POST /api/committee/<code>/chat/permissions/bulk-add/` and `bulk-remove/`

**Kai member permission system (2026-06-03):** New `KaiMemberPermission` model (migration 0188) — granular, additive access control for Kai committee members who are not chairs. Default is **no access**. Seven independent permission flags: `can_view_report_list`, `can_view_report_details`, `can_view_submitter_identity`, `can_view_accused_identity`, `can_edit_open_cases`, `can_add_activity`, `can_close_cases`. Chairs always have full access regardless. New `/committee/<code>/kai-permissions/` page (chair-gated, linked from committee home) shows all non-chair members in a permission grid — inline checkbox updates fire `update_kai_member_permission` via AJAX, "Reset All to No Access" fires `reset_kai_permissions`. **Auto-reset on exec change**: `signals.py` extended — when any role tied to a Kai committee's `.role` FK changes holders (`m2m_changed post_add/post_remove`), all `KaiMemberPermission` rows for that committee are wiped, and all user-specific `ChatChannelPermission` rows for the committee's chat channel are also cleared. This ensures a new Kai chair inherits a clean slate.

**Custom channel settings overhaul (2026-06-03):** `edit_channel.html` fully redesigned — card-based layout replacing the single flat form; Basic Info, Channel Status, and Access cards. Channel Status card exposes two new toggles: **Active** (hides the channel from all members when off) and **Read-Only** (prevents new messages, useful for announcement channels). New **Alumni only** special-role permission type added. Delete Channel link moved into the edit page header so admins don't have to hunt for it. After saving, the page now redirects back to the edit form (not the channel list) so you can keep adjusting. Alumni-only access is enforced in `has_access`. `is_read_only` short-circuits `can_write` before any other check. Migration 0187 adds `ChatChannel.is_read_only` and `ChatChannelPermission.alumni_only`.

**Guest permission expiry + `can_edit` (2026-06-03):** Two new fields on `ChatChannelPermission` (migration 0186). `can_edit` (default `False`) gates whether a guest can edit their own messages — enforced in `edit_channel_message` so committee members always pass but guests need the flag. `expires_at` (nullable `DateTimeField`) makes access temporary — all `can_read/write/delete/edit_messages` channel methods filter out expired rows, and an `is_expired` property is exposed for template rendering. UI additions: "Can Edit Own" toggle and "Access Expiry" datetime picker in the Add Guests panel; "Edit" column in the guests table; expiry badge per row (orange = future, red = expired) with an inline editor (Save / Clear) that fires `update_guest_permission`. New `prune_expired_chat_permissions` Celery task (nightly 3:12 AM CST) and matching `prune_expired_chat_permissions` management command with `--dry-run` flag clean up stale rows automatically.

**QoL — auto-remove voting member on member removal (2026-06-03):** When a user is removed from committee members via `committee_remove_member`, they are also automatically removed from `voting_members` if present. Prevents a confusing orphan state where a non-member retains a vote.

**`committee_detail` page removed (2026-06-03):** The committee detail page (`/committee/<code>/details/`) has been archived. All internal references (`{% url 'committee_detail' %}` in 9 templates, `redirect('committee_detail')` in 8 view files) updated to point to `committee_home`. The old URL now returns a 301 permanent redirect to `committee_home` to preserve any bookmarks. `committee_detail.py` view file retained as an archive; removed from `__init__.py` and `urls.py` imports.

**Kai permissions page overhaul (2026-06-03):** `manage_kai_permissions` now shows all committee members — chairs appear with all checkboxes checked and greyed out (disabled) with a "Chair" badge, making it clear they have inherent full access. Previously the page excluded chairs from the table entirely and only queried `committee.members`, missing anyone added via `voting_members`. Query updated to union `members | voting_members` and the `update_kai_member_permission` endpoint updated to match. Member search bar added. The `-m-8 p-8` layout pattern across all five Kai admin templates (`view_reports.html`, `manage_templates.html`, `create_template.html`, `edit_template.html`, `manage_report.html`) was causing the officer toolbar to be visually hidden (content overflowed upward into it) and the page to horizontally scroll; replaced with standard `min-h-screen` outer div and `px-4 sm:px-6 lg:px-8 py-8` on the inner container.

**Kai dashboard consolidated into reports page (2026-06-03):** `kai_dashboard` view is now a redirect to `view_kai_reports`. All analytics context (status counts, category bar chart, monthly submission trend line, deliberation outcomes, recent activity feed) merged into `view_kai_reports`. `view_reports.html` redesigned: four clickable stat cards at the top act as status filters (replacing the separate tab bar), a collapsible "Analytics" accordion holds the charts and deliberation/activity data (charts initialize lazily on first open), action buttons (Member Perms, Form Builder, Templates, Export CSV, Submit) consolidated into the page header. Old `dashboard.html` archived to `templates/kai/archive/`. All `kai_dashboard` URL references in `admin_base.html`, `form_builder.html`, `user_dashboard.html`, and `seed_admin_v2.py` updated to `view_kai_reports`.

**Files changed:** `templates/base.html`, `templates/admin_base.html`, `templates/admin_v2/base.html`, `templates/admin_v2/dashboard.html`, `templates/chat/channel.html`, `templates/committee/manage_chat_permissions.html`, `templates/committee/attendance_history.html`, `templates/committee/push_to_chapter.html`, `templates/committee/vote.html`, `templates/committee/documents.html`, `templates/committee/attendance.html`, `templates/committee/manage_members.html`, `templates/committee/committee_index.html`, `templates/committee/upload_document.html`, `src/view/committee/manage_chat_permissions.py`, `src/view/committee/committee_index.py`, `src/view/committee/add_member.py`, `src/view/committee/remove_member.py`, `src/view/committee/edit_committee_chat.py`, `src/view/committee/create_vote.py`, `src/view/committee/push_to_chapter.py`, `src/view/committee/unpush_from_chapter.py`, `src/view/committee/upload_document.py`, `src/view/committee/__init__.py`, `src/urls.py`, `src/models/users.py` (27 admin_v2 templates + 31 officer templates migrated)

---

### v2.31.0 - Page Visit Analytics & Constitution & Bylaws Builder (2026-06-04)

**Page visit tracking (Admin v2):** `PageVisit` model (migration 0191) tracks per-user, per-path visit counts. A `sendBeacon` fires post-load in `base.html` — non-blocking, fires even on navigate-away. Single PostgreSQL `INSERT ... ON CONFLICT DO UPDATE` upsert (no race conditions, one DB round-trip). Admin v2 dashboard at `/admin-v2/page-visits/` shows sortable aggregate and per-user drill-down. Admin paths excluded.

**Constitution & Bylaws Builder (foundation — v2.31.0, migration 0192):** Full in-app structured C&B document system replacing the static `constitution_bylaws.html` template. The `/constitution-bylaws/` URL now renders from the database.

- **New models** in `src/models/cnb.py`: `GoverningDocument`, `Article`, `Section`, `Resolution`, `ResolutionAmendment`
- **`GoverningDocument`** — Constitution or Bylaws record (title, preamble, last_reviewed)
- **`Article`** — Article within a document (number, title, activatable/deactivatable with reason + audit trail)
- **`Section`** — Section within an article (stores actual text, activatable/deactivatable, amendment protection tracking with expiry date and auto-generated note)
- **`Resolution`** — In-progress resolution builder (separate from legacy `PassedResolution`). Stores WHEREAS clauses (one per line, auto-formatted), BE IT RESOLVED text, type (amendment/general/emergency), status (draft → pending → passed/failed/withdrawn), configurable protection period (default 180 days)
- **`ResolutionAmendment`** — Links a resolution to a specific section + proposed replacement text. Auto-captures the section's current text as a snapshot at creation time for side-by-side diff display.
- **`Resolution.apply_amendments()`** — When a resolution passes: applies all proposed texts to their sections, clears protection flags
- **`Resolution.apply_failure_protection()`** — When a resolution fails: locks all targeted sections for `protection_days` days; auto-fills `protection_note` with the resolution title and dates
- **New permission**: `ParliamentUser.has_cnb_permission` — True if admin OR has `CNB` role. `cnb_required` decorator added to `src/decorators.py`
- **CNB role** added to `Role.DEFAULT_ROLES` (ID 10, code `CNB`, name "Constitution & Bylaws Chair") — restored by `restore_committees_and_roles` management command
- **Views** (`src/view/officer/cnb.py`): CNB viewer, dashboard, manage document, edit section, toggle section/article active, resolution list/detail/create/edit, add/remove amendment, set status, section context JSON API
- **Templates** (`templates/cnb/`): viewer, dashboard, manage_document, edit_section, resolution_list, resolution_detail, resolution_form — all dark mode aware, gradient banners, primary color scale
- **Navigation**: "C&B Manager" link added to officer_home.html (gated on `has_cnb_permission`) and admin_base.html sidebar

**What's not built yet (next session):** Seeder management command to populate the initial document text from the existing static template content; section anchor links for deep-linking from resolutions pages.

---

### v2.30.1 - KaiMemberPermission Enforcement (2026-06-03)

Wires the seven `KaiMemberPermission` flags into actual view and template enforcement. Previously the model and management UI existed but all Kai views still gated solely on `is_chair or is_admin`, making the granular flags inert.

**View-level access gates** — non-chairs with no permission row are redirected to home:
- `view_kai_reports` and `export_kai_reports_csv` → `can_view_report_list`
- `manage_kai_report` and `print_kai_report` → `can_view_report_details`
- `bulk_actions_kai_reports` → `can_view_report_list` at entry; per-action checks gate further

**Action-level gates within `manage_kai_report`** — wrong permission returns a redirect with an error message, not a 403 page:
- Mark reviewed/pending, update deliberation, update tags, link/unlink reports, update accused, notify accused/submitter → `can_edit_open_cases`
- Add activity, update notes → `can_add_activity`
- Archive, approve/deny closure requests → `can_close_cases`

**Bulk action gates** in `bulk_actions_kai_reports`:
- `mark_reviewed` / `mark_pending` → `can_edit_open_cases`
- `archive` → `can_close_cases`

**Identity redaction in templates** — sensitive names are hidden rather than raising an error, so members can see a report exists without learning who filed it or who it targets:
- Submitter name in report list and detail sidebar → shows "Anonymous" / "Redacted" without `can_view_submitter_identity`
- Accused name in detail "Directed To" section and notify-accused form → shows "Redacted" without `can_view_accused_identity`
- Notify Accused section hidden without both `can_edit_open_cases` and `can_view_accused_identity`
- Notify Submitter section hidden without both `can_edit_open_cases` and `can_view_submitter_identity`
- Submitter name in Related Reports list → "Anonymous" without `can_view_submitter_identity`
- CSV export redacts submitter and accused columns for members lacking the identity flags

**Helper added:** `_get_kai_access(user, committee)` in `kai_reports.py` — returns a dict of all seven boolean flags. Chairs and admins always get all `True`. Other users get their `KaiMemberPermission` row values; users with no row get all `False`. Passed as `kai_access` in context to all affected views so templates can conditionally render sections.

**Files changed:** `src/view/kai_reports.py`, `src/models/__init__.py` (added `KaiMemberPermission` import), `templates/kai/view_reports.html`, `templates/kai/manage_report.html`

---

### v3.2.0 — API Token Security Overhaul (2026-06-08)

**Type:** Security / New Feature

Complete replacement of the DRF token system with a custom-built API token infrastructure designed for audit-readiness, granular access control, and admin oversight.

---

#### Token Model & Status Workflow

**New model `APIToken` (`src/models/api.py`):**
- 64-character hex key (`secrets.token_hex(32)`)
- Four statuses: `pending` → `active`, `revoked`, or `rejected`
- `scopes` — JSONField list of allowed scope keys per token
- `request_note` — free-text rationale the member provides when requesting
- Full audit trail: `approved_by/at`, `revoked_by/at/reason`, `rejection_reason`, `last_used_at`, `expires_at`

**Defined scopes:**

| Key | Purpose |
|-----|---------|
| `members:read` | Read member directory |
| `events:read` | Read events |
| `legislation:read` | Read legislation |
| `committees:read` | Read committee list |
| `attendance:read` | Read attendance records |

**New model `APIAccessLog`** — written after every API request:
- FKs to `APIToken` (nullable) and `ParliamentUser` (nullable) so logs survive deletion
- Denormalized `username` and `token_key_prefix` (8 chars) for non-repudiation
- Fields: `endpoint`, `method`, `ip_address`, `response_status`, `scopes_used`

**Migration 0202** — creates both tables; RunPython copies all existing DRF `authtoken_token` rows to `APIToken` as `active` with all scopes.

---

#### Authentication & Permissions

**`src/api/authentication.py` — `APITokenAuthentication`:**
- Parses `Authorization: Token <key>` header
- Validates token status and expiry before authenticating
- Stamps `last_used_at` via `.update()` (no full-row save)
- Attaches token to `request._api_token` for downstream scope checks

**`src/api/permissions.py`:**
- `APIEnabled` — gates all API access behind the `rest_api` feature flag
- `ScopePermission` — reads `view.required_scope`, checks `request._api_token.has_scope()`

**`src/api/views.py` — `APILoggingMixin`:**
- Overrides `finalize_response()` to write `APIAccessLog` after every request
- Exceptions are swallowed so logging never breaks API response delivery
- All 5 viewsets updated with `authentication_classes`, `permission_classes`, `required_scope`, and `APILoggingMixin`

---

#### User-Facing Token Management

**New views in `src/view/api.py`:**
- `request_api_token` — creates token as `pending` or `active` depending on `api_token_auto_approve` feature flag; blocks duplicate requests
- `revoke_api_token` — user revokes their own token by ID

**Updated `templates/preferences.html` — Developer API section redesign:**
- Rebuilt as a proper section matching the rest of the preferences page (card layout, icon heading)
- State machine: no-token/rejected → request form with scope checkbox cards; pending → amber waiting banner; active → token key card with details, scopes grid, and revoke row
- "Docs →" link to the developer guide inline in the heading

**Updated `templates/guide/members/developer_api.html`:**
- New Section 5 "Setting Up Your First Integration" with Python/requests, JS/fetch, bash/curl+jq, and Postman examples
- Section 2 updated to reflect the request/approval flow

---

#### Admin Token Management

**New admin views (`src/view/api.py`):**
- `admin_api_tokens` — list all tokens with status filter tabs; loads `rest_api` and `api_token_auto_approve` feature flag objects
- `admin_approve_token` — approve a pending token (POST)
- `admin_reject_token` — reject with optional reason (POST)
- `admin_revoke_token` — revoke an active token with optional reason (POST)
- `admin_update_token_scopes` — edit scope list on any token (POST)
- `admin_api_token_logs` — view paginated access logs for a specific token (last 200 entries)
- `admin_toggle_api_flag` — JSON toggle for `rest_api` or `api_token_auto_approve` flags in-place (no redirect)

**New `templates/admin_v2/api_tokens.html`:**
- API Settings panel with live toggle switches for "REST API Enabled" and "Auto-Approve Tokens"
- Status filter tabs (All / Pending / Active / Revoked / Rejected)
- Token table: user, name, status badge, scopes, created, last used, actions
- Modals: Reject (with reason), Revoke (with reason), Edit Scopes (checkboxes)
- JS toggle switches update pill color and dot position without page reload

**New `templates/admin_v2/api_token_logs.html`:**
- Token detail card (status, scopes, audit trail)
- Per-request log table: timestamp, endpoint, method, IP, status code, scopes used

**Admin-v2 dashboard** — new indigo API card showing Active/Pending/Requests 24h counters; pending subtitle turns amber when > 0 pending.

**Admin-v2 subnav** — "API" link added; highlights when `api_token` in URL name.

---

#### Feature Flags & Maintenance

**`seed_feature_flags.py`** — Added `api_token_auto_approve` (category: `admin`, default: `False`).

**`src/tasks.py`** — `cleanup_api_access_logs` Celery task deletes `APIAccessLog` rows older than 90 days.

**Deploy notes:**
```
python manage.py migrate           # 0202_apitoken_apiaccesslog
python manage.py seed_feature_flags
python manage.py collectstatic --noinput
systemctl restart parliament-gunicorn parliament-worker
```

**Files changed:** `src/models/api.py` (new), `src/models/__init__.py`, `src/migrations/0202_apitoken_apiaccesslog.py` (new), `src/api/authentication.py` (new), `src/api/permissions.py` (new), `src/api/views.py`, `src/view/api.py`, `src/urls.py`, `src/tasks.py`, `src/management/commands/seed_feature_flags.py`, `src/view/admin_v2.py`, `templates/admin_v2/base.html`, `templates/admin_v2/dashboard.html`, `templates/admin_v2/api_tokens.html` (new), `templates/admin_v2/api_token_logs.html` (new), `templates/preferences.html`, `templates/guide/members/developer_api.html`

---

### v3.1.3 — `update_fields` Sweep, Test Fixes & Component CSS (2026-06-07)

**Type:** Performance / Maintenance

- **`update_fields` sweep** — ~188 `.save()` calls across 20+ view files now scope DB writes to only the modified columns. Down from 302 bare saves to 114 (remaining are form saves, INSERTs, or multi-branch conditionals).
- **9 pre-existing test fixes** — all 57 tests now pass. Fixes: `test_committee_is_member` (chairs/members are separate M2Ms), `test_committee_detail_view` (RedirectView needs `follow=True`), `test_present_member_can_vote` (use `status='present'` not `present=True`), 4 ProfileTestCase failures (string `user_id` coerced to int by `ActivityLog`), `test_preferred_name_clear`, `test_get_display_name_without_preferred`, `test_legislation_history_shows_user_legislation`, `test_unique_username_constraint`.
- **Bug fix** — `profile_view.py`: clearing preferred name stored `None` (NOT NULL violation); fixed to store `''`.
- **Component CSS** — `@layer components` block added to `tailwind-input.css` with `btn-primary/secondary/danger/ghost`, `btn-sm/lg`, `badge/badge-green/red/yellow/blue/purple`, `card`, `form-input`. Eight high-traffic templates updated to use them.

**Files changed:** `static/css/tailwind-input.css`, `static/css/tailwind.css`, `src/view/profile_view.py`, `src/test_comprehensive.py`, `src/test_edge_cases.py`, `src/view/kai_reports.py`, `src/view/admin_v2.py`, `src/view/officer/cnb.py`, `src/view/officer/manage_announcements.py`, `src/view/slating/` (6 files), `src/view/service_hours.py`, `src/view/guide.py`, `src/view/two_factor.py`, `src/view/chat/channel_chat.py`, `src/view/kai_form_builder.py`, `src/view/edit_legislation.py`, `src/view/officer/chapter_minutes.py`, `src/view/committee/committee_minutes_editor.py`, `templates/vote.html`, `templates/passed_legislation.html`, `templates/directory.html`, `templates/announcements.html`, `templates/profile.html`, `templates/preferences.html`, `templates/home_modern.html`, `templates/calendar.html`

---

### v3.1.2 — CSP `unsafe-inline` Removal (2026-06-06) ✅ Deployed

Completes the multi-session inline handler migration. Every `onclick=`, `onchange=`, `onsubmit=`, and `oninput=` attribute has been removed from all templates and replaced with `addEventListener` calls inside nonced `<script>` blocks. With no remaining inline handlers, `'unsafe-inline'` can be dropped from `script-src` entirely.

**Type:** Security

**Security:**
- **`src/middleware/security.py`** — `'unsafe-inline'` removed from `script-src`. The directive is now `script-src 'self' 'nonce-{nonce}'` (plus the Cloudflare beacon domain when applicable). Any script injected into the page — even one that bypasses input sanitization — is blocked by the browser because it will not carry the per-request nonce.

**Templates changed** (inline handler → `addEventListener` migration, this session):
- `committee/upload_document.html`, `committee/create_vote.html`, `committee/documents.html`, `committee/push_to_chapter.html`, `committee/manage_members.html`
- `officer/attendance_dashboard.html`, `officer/member_attendance_detail.html`
- `legislation_history.html`, `changelog.html`, `my_attendance.html`, `my_excuses.html`
- `kai/print_report.html`, `kai/manage_templates.html`, `kai/view_reports.html`
- `manage_chapter_document.html`, `cnb/resolution_print.html`
- `slating/view_application.html`, `slating/my_applications.html`
- `errors/pledge_restricted.html`, `admin/migrate_user_id.html`

**Security:**
- **`src/view/landing.py`** — Added IP-based rate limiting to `contact_submit` (5 submissions per IP per 10 minutes via Django cache). Prevents email flood attacks against officer addresses passed as `recipient_email`.
- **CSP Violation Analytics** — New `CSPViolation` model stores browser violation reports with proper indexed fields (`violated_directive`, `blocked_uri`). The `/csp-report/` endpoint now writes to this model instead of `SecurityNotificationLog`. New admin-v2 page at `/admin-v2/security/csp-violations/` groups violations by type with a dismiss-as-false-positive button. Security dashboard stat card and tool tile added. Requires migration `0201_cspviolation`.
- **Vote Auto-Close moved to Celery only** — The on-page-load auto-close blocks in `vote_view.py` and `committee/vote.py` have been removed. `auto_open_close_chapter_votes` and `auto_open_close_committee_votes` (already running every minute via Celery Beat) are now the sole source of truth. Each close is wrapped in `transaction.atomic()` with `select_for_update()` so a mid-loop crash cannot leave a bill in a partial state.
- **`update_fields` on high-frequency saves** — `end_vote.py`, `tasks.py` (chapter + committee vote close tasks), `committee/vote.py` (manual end + recalculate), `passed_legislation.py` (add legislation), and `profile_view.py` (all user profile save paths) now specify `update_fields` to avoid full-row writes on frequently updated models (`Legislation`, `ParliamentUser`).
- **`end_vote.py` bug fixes** — `plurality_options` was iterated without a `None` guard (now uses `or []`). Individual voter lists (`in_favor`, `against`, `abstain`) were always passed in template context even for anonymous votes; they are now only added when `anonymous_vote=False`.

**Performance:**
- **`src/view/passed_legislation.py`** — Replaced 3–4 per-legislation `Vote.objects.filter().count()` calls with a single annotated queryset (`yes_count`, `no_count`, `abstain_count`, `total_count`). Reduces page-load query count from ~60–80 to ~1 for a full 20-item page.
- **`src/view/kai_reports.py`** — Replaced two per-category `.count()` loops (~14 queries total) with a single `values().annotate(total=Count('id'))` query. Both `category_counts` and `category_data` are now built from the same aggregated result.
- **`src/view/admin_v2.py`** — Batch `IPWhitelist`/`IPBlacklist` lookups in the user security view: two `__in` set queries before the loop instead of two queries per unique IP. Celery dashboard now evaluates `PeriodicTask` queryset once with `list()` and uses `len()`/sum comprehension instead of three separate DB hits. Feature flag grouping now uses one `order_by` query + Python `setdefault` instead of two queries per category.
- **`src/view/calendar.py`** — Replaced `exists()` + `first()` double query on `AttendanceExcuse` with a single `first()` call.

---

### v3.1.1 — Security & Performance Fixes (2026-06-06) ✅ Deployed

Patch release addressing all findings from the automated code review of v3.1.0. Security fixes, performance improvements, async email migration, and wildcard import cleanup.

**Security:**
- `middleware/security.py` — Passkey authenticate complete endpoint now covered by rate limiter (was previously unprotected, allowing unlimited brute-force attempts)
- `view/webauthn.py` — User verification changed from `PREFERRED` to `REQUIRED` on both register and authenticate; removed dead `csrf_exempt` import
- `apps.py` — `CRYPTOGRAPHY_KEY` validated at startup via `ImproperlyConfigured`; silent failure on missing env var is no longer possible

**Performance:**
- `tasks.py` — `send_event_reminder_pushes`: added `select_related('preferences')` to eliminate N+1 (up to 50 extra queries per task run); visibility filter moved to DB-level queryset filter; redundant `eligible_users.count()` after loop removed
- `signals.py` — `sync_exec_committee_on_role_change` receiver moved `sender=ParliamentUser.roles.through` filter to the decorator, eliminating per-signal-change function call overhead

**Form Validation:**
- `forms.py` — Added `clean()` method to `EventForm` enforcing `reminder_hours_before` in range 1–168 server-side (HTML `min/max` attributes are bypass-able)

**Code Quality:**
- `view/kai_reports.py`, `view/kai_user_dashboard.py`, `view/service_user_dashboard.py`, `view/profile_view.py` — Synchronous `send_mail()` calls replaced with `send_email.delay()` (async via Celery)
- `view/end_vote.py`, `view/upload_legislation.py`, `view/vote_view.py`, and others — Wildcard `from ..decorators import *` / `from ..models import *` replaced with explicit imports

---

### v3.1.0 — Event Push Reminder Notifications (2026-06-05) ✅ Deployed

Per-event push notification reminders with global on/off controls in Admin v2. Officers enable a reminder on any event and set the lead time; a Celery Beat task fires every 15 minutes to dispatch reminders on schedule. Respects master feature flags and per-user opt-out.

---

### v3.0.0 - WebSocket Chat, Passkeys, C&B Resolution Builder & Security Hardening (2026-06-05) ✅ Deployed

Major version release replacing HTTP polling with persistent WebSocket connections, overhauling the chat UI, adding passkeys, a comprehensive overhaul of the C&B resolution builder with word-level diff and amendment tracking, and targeted security hardening across the admin and chat layers.

---

#### C&B Resolution Builder — Amendment Type System (migration 0195)

Formalizes what kind of change each resolution amendment makes and where it applies.

**Model changes (`src/models/cnb.py`):**
- `ResolutionAmendment.amendment_type` — `CharField` with choices: `change` / `addition` / `deletion`; default `change`
- `ResolutionAmendment.scope_note` — optional `CharField(300)` for specifying a sub-item (`§ 3.a.i`, `second sentence`, etc.)
- `proposed_text` set to `blank=True` to support whole-section deletions (no replacement text needed)

**Auto-detection in view (`add_amendment`):**
Type is inferred from the submitted text rather than a user-selected radio — no UI friction:
- Empty `proposed_text` → `deletion`
- `original_text` is a substring of `proposed_text` → `addition`
- Otherwise → `change`

**`apply_amendments` logic:**
- Whole-section deletion (`deletion` + no `scope_note` + no `proposed_text`) → clears content, suspends section, sets `deactivation_reason`
- All other cases → writes `proposed_text` to section content

**Files changed:** `src/models/cnb.py`, `src/migrations/0195_resolutionamendment_amendment_type_scope_note.py` (new), `src/view/officer/cnb.py`

---

#### Amendment Editor on Edit Page

The full amendment editor (section selector, original/proposed text fields, diff preview, tracked amendment cards) was previously only on the detail page. It is now also present on the **edit resolution page** so writers can manage amendments while drafting.

**Changes:**
- Amendment cards added after the form in `resolution_form.html` — shows existing amendments with type badge, scope note, before/after diff, and "Copy [ref]" button per card
- Add Amendment modal added with section selector (uses `ref_docs` context already in view — no view changes needed)
- Redirect control: `<input type="hidden" name="next" value="edit">` in both the add and remove forms so actions from the edit page redirect back to the edit page instead of the detail page
- `add_amendment` and `remove_amendment` views both check `request.POST.get('next') == 'edit'` to select redirect target

**Files changed:** `templates/cnb/resolution_form.html`, `src/view/officer/cnb.py`

---

#### Word-Level Diff Engine (Tracked Changes Preview)

Live tracked-changes preview in the amendment editor modal using a word-level LCS (Longest Common Subsequence) diff algorithm — shows exactly which words were added or removed between the original and proposed section text.

**Algorithm details:**
- Tokenizer: `/\s*\S+/g` — each token includes its preceding whitespace to prevent space tokens from being matched across locations (fixes a prior bug where tokens like "Pi." would appear between unrelated words)
- Similarity threshold: if fewer than 40% of tokens are shared, falls back to a clean block replacement view instead of showing a garbled interleaved diff (handles complete text rewrites)
- Renders: unchanged text normally, additions in green, deletions in red strikethrough

**Files changed:** `templates/cnb/resolution_form.html`, `templates/cnb/resolution_detail.html`

---

#### Copy [ref] Citation System

Replaces the "Insert at cursor" button in the C&B reference drawer with a clipboard copy button that generates a bracketed reference code. The code can be pasted anywhere in the form, as many times as needed.

**Code format:**
- Basic: `[Constitution Art. III § 2]`
- With type (non-change): `[Constitution Art. III § 2 (Addition)]`
- With scope note: `[Constitution Art. III § 2 (Addition) — second sentence]`
- With sequential counter (from amendment cards): `[Constitution Art. III § 2 (Addition) - 1]`

The counter increments per amendment in display order, making each code unique even for the same section. Visual copy feedback: button text flips to "Copied!" in green for 1.8 s.

**Files changed:** `templates/cnb/resolution_form.html`

---

#### Protected Citation Markers

Pasted `[ref]` codes in all textareas are protected from accidental editing or partial deletion.

**Body textarea (Section II):**
- Full protection — markers can only be removed via the × chip button in the "Cited Sections" panel below the textarea
- Pasting new markers is allowed; the panel updates immediately and adds a new chip
- Each chip is per-occurrence (not deduplicated) — clicking × removes only the first occurrence of that marker, leaving duplicates intact
- ▾ expand button shows a section text preview pulled from the drawer DOM

**Other textareas (whereas, resolved, notes — Sections I & III):**
- Same full protection via `setupMarkerArea()` — a generic function wired to each textarea by ID
- Each textarea has its own "Cited Sections" chip panel below it (same × removal mechanic, no text preview)
- Markers can only be removed via their × chip

**Files changed:** `templates/cnb/resolution_form.html`

---

#### PDF Preview Button

A "Preview PDF" button is now shown in the edit page header (edit mode only). Opens the existing `resolution_print` view in a new tab. Tooltip prompts "Cmd+P / Ctrl+P to save as PDF."

**Files changed:** `templates/cnb/resolution_form.html`

---

#### Print View — Inline Amendment Callouts & Appendix

The resolution print/PDF view now processes `[ref]` codes in the body text and renders a proper amendment-aware document.

**Inline callouts (where the marker appears in body text):**
Each `[ref]` code is replaced by a small indented callout block showing:
- Section identifier + amendment type in bold (e.g., `Constitution Art. I § 1 (Addition)`)
- Only the delta text — the specific words added, changed, or deleted — not the full section. Computed client-side via the LCS diff algorithm.
- Added text in green, deleted text in red strikethrough

**Appendix (appended to document by JS):**
For each amendment, a full entry showing:
- Section identifier, title, and amendment type
- Descriptive context sentence in formal resolution style: *"The following shall be added to Article I (Name and Purpose) of the Bylaws of the Samford Chapter, the Alpha Mu of Beta Theta Pi, under § 1: Name:"*
- Full word-level diff of original vs. proposed text with green/red highlights

The appendix is built entirely in JavaScript using the amendments data array rendered into the page by Django. This avoids Django template queryset double-evaluation issues that caused the appendix to silently not render.

**Files changed:** `templates/cnb/resolution_print.html`

---

#### WebSocket Chat (Django Channels)

Replaces HTTP long-polling for chat messages with a persistent WebSocket connection using Django Channels + Redis channel layer. Each chat channel gets a group (`chat_{id}`); `ChatConsumer` enforces read permissions on connect and receives broadcast events from the HTTP send/edit/delete views. The HTTP endpoints are unchanged — they still handle auth, CSRF, and push-notification dispatch, then call `_ws_broadcast()` after saving.

**Client changes (`templates/chat/channel.html`):**
- Connects at `wss://<host>/ws/chat/<channel_id>/` on load; auto-reconnects with exponential backoff (1s → 30s cap)
- Status dot: green "Live" (connected), yellow "Reconnecting…" (backoff), red "Connection error"
- `onmessage` handles three event types: `message` (append), `edit` (update text + `(edited)` label), `delete` (remove from DOM)
- Active users list still HTTP-polled (lightweight, not real-time critical)
- Optimistic send: temp element appended immediately on submit; HTTP success removes the temp; WS echo creates the real element — no race condition

**Server changes:**
- `Parliament/asgi.py` — `ProtocolTypeRouter` routes HTTP to Django, WebSocket to `AuthMiddlewareStack(URLRouter(...))`
- `src/consumers.py` — `ChatConsumer(AsyncWebsocketConsumer)`: checks `can_read` on connect, joins group; handles `chat.message`, `chat.edit`, `chat.delete` broadcast events
- `src/routing.py` — WebSocket URL pattern: `ws/chat/<channel_id>/`
- `src/view/chat/channel_chat.py` — `_ws_broadcast()` helper added; called after send, edit, and delete to push events to the group
- `Parliament/settings_postgres.py` — `daphne` added first in `INSTALLED_APPS`; `channels` added; `ASGI_APPLICATION` set; `CHANNEL_LAYERS` gated on `REDIS_URL and not DEBUG` (InMemoryChannelLayer used in dev)
- `requirements.txt` — added `channels==4.2.0`, `channels-redis==4.2.0`, `daphne==4.1.2`

**Deployment — required steps before going live:**
```bash
pip install channels channels-redis daphne
# Switch process manager from Gunicorn to Daphne:
# Edit parliament-gunicorn.service ExecStart:
#   was: gunicorn Parliament.wsgi:application ...
#   now: daphne -u /run/parliament.sock Parliament.asgi:application
# Add nginx WebSocket proxy headers to the location block:
#   proxy_http_version 1.1;
#   proxy_set_header Upgrade $http_upgrade;
#   proxy_set_header Connection "upgrade";
systemctl daemon-reload
systemctl restart parliament-gunicorn
```

---

#### Discord-Style Message Stacking

Consecutive messages from the same user within 15 minutes are stacked into a compact group — no repeated avatar or name, just the message text. Groups are visually separated by extra spacing.

**How it works:**
- Server-rendered messages get `data-sender-id`, `data-timestamp`, and `data-raw` attributes
- `stackMessages()` runs on `DOMContentLoaded` and assigns each row a role: `solo`, `first`, `middle`, or `last` using a look-ahead `stacksWith(a, b)` comparison
- `convertToStacked()` mutates server-rendered rows: hides avatar + header, moves edit/delete buttons inline beside the message text, applies compact padding
- `appendMessage()` uses live `lastMsgSenderId`/`lastMsgTime` tracking variables to determine stacking for newly received WS messages
- `prependMessage()` (load-older path) updated to include edit button, delete button, and `data-raw` — previously these were missing

---

#### Continuous Left Border Accent

Own messages have a left border accent (`border-l-4 border-blue-400`). Previously each row applied `rounded-lg` which caused the border to curve inward between stacked rows, making it appear as disconnected segments.

**Fix:** `stackMessages()` applies precise border-radius per role — `rounded-t-lg` for first, no rounding for middle, `rounded-b-lg` for last, `rounded-lg` for solo — so the left border runs as a single unbroken bar through the full group.

---

#### Edit Button & Flow Fixes

Several independent bugs in the edit flow were fixed:

- **Timezone bug removed:** Client-side 1-hour edit check compared CST server timestamps against the local browser clock. For users outside CST (e.g. Europe), this always evaluated as >1 hour and blocked editing. Client check removed entirely — the server enforces the 1-hour window with a 403 response.
- **`data-raw` for accurate re-editing:** `editMessage()` previously read `messageElement.textContent`, which included `<br>` tags injected by `renderMessageText()`. Messages with newlines would re-open with escaped HTML. Fixed by storing the original text in `data-raw` on the message element; `editMessage()` reads `dataset.raw` and `saveEdit()` updates `data-raw` after a successful save.
- **Send icon direction:** SVG path naturally points right; removed incorrect rotation that was pointing it down.
- **`prependMessage` completeness:** Load-older path now includes edit button (sender only), delete button (admin/chair/sender), `data-raw` attribute, `msg-row` class, and inline padding — matching the `appendMessage` path.

---

#### Passkeys (WebAuthn)

Optional fast-path login that bypasses both password and 2FA. Users register passkeys from their profile and can sign in with Face ID, Touch ID, or their device PIN — no TOTP code required. Multiple passkeys per user are supported (e.g. phone + laptop). The existing username/password + TOTP flow is completely unchanged.

**How it works:**
- On registration, the browser generates a public/private key pair; the public key is stored in `WebAuthnCredential`. The private key never leaves the device.
- On authentication, the browser signs a server-issued challenge with the private key; the server verifies the signature against the stored public key.
- Successful passkey auth calls Django's `login()` and sets `webauthn_authenticated = True` in the session. `Enforce2FAMiddleware` checks this flag and skips the TOTP verify step.
- If the user also has a TOTP device, `otp_login()` is called so `is_verified()` returns True for any code that checks it directly.

**New model — `WebAuthnCredential`:**
- `credential_id` (BinaryField, unique) — raw credential ID from the authenticator
- `public_key` (BinaryField) — COSE-encoded public key
- `sign_count` (PositiveIntegerField) — incremented on each use; replay attack detection
- `name` (CharField) — user-assigned display name (e.g. "iPhone 15")
- `aaguid` (CharField) — authenticator model identifier
- `created_at`, `last_used_at` — timestamps

**New views (`src/view/webauthn.py`):**
- `passkey_register_begin` / `passkey_register_complete` — authenticated; adds a passkey from the profile page
- `passkey_authenticate_begin` / `passkey_authenticate_complete` — unauthenticated; signs in via passkey on the login page
- `passkey_delete` — removes one of the user's passkeys

**New URLs:** `/accounts/passkeys/register/begin|complete/`, `/accounts/passkeys/authenticate/begin|complete/`, `/accounts/passkeys/<pk>/delete/`

**Login page:** "Sign in with a Passkey" button with divider; hidden automatically if the browser doesn't support WebAuthn.

**Profile page:** Passkeys accordion (between Two-Factor Authentication and Change Password) showing registered passkeys with add/remove controls. Badge shows count when passkeys are registered.

**Middleware:** `Enforce2FAMiddleware` updated — checks `webauthn_authenticated` session flag before redirecting to TOTP verify; `/accounts/passkeys/authenticate/` added to exempt paths.

**Deployment:**
```bash
pip install webauthn==2.7.1
python manage.py migrate   # migration 0189
```

**Files changed:** `requirements.txt`, `src/models/webauthn.py` (new), `src/models/__init__.py`, `src/migrations/0189_webauthn_credential.py` (new), `src/view/webauthn.py` (new), `src/urls.py`, `src/middleware/two_factor.py`, `src/view/profile_view.py`, `templates/profile.html`, `templates/registration/login.html`

---

#### Chat Polish & Date Separators

Several UI improvements built on top of the WebSocket foundation.

**Features added:**
- **Date separators** — horizontal rule with date label (`Today`, `Yesterday`, `Mon Jun 2`, etc.) inserted between messages from different days; idempotent insertion for both initial load and load-older
- **Jump-to-bottom button** — arrow button anchored to the bottom-right of the message area; appears when scrolled more than 150 px above the bottom; animated in/out
- **Typing indicator** — `{"type":"typing"}` WS event sent from client (debounced 2.5 s) on keypress; server broadcasts to group; clients show `X is typing…` below the message list, auto-clears after 4 s of silence per user, hidden for own events
- **Inline error toasts** — `showChatError(msg)` replaces all `alert()` calls with a dismissing bar above the input box

**Fixes:**
- Edit flow `style.display` reset changed from `'block'` to `''` so stacked-row flex layout is preserved after cancel
- `stackMessages()` + `insertInitialDateSeparators()` re-run after `loadOlderMessages` so newly prepended messages get correct stacking and separators
- `convertToStacked()` guarded with `data-stack-converted` to prevent double-conversion on re-runs

**Files changed:** `src/consumers.py`, `templates/chat/channel.html`

---

#### Admin Dashboard — Chat Settings Update

The Chat Settings section of Admin v2 now reflects v3.0.0's WebSocket architecture.

**Changed:**
- Removed two dead settings from the seed command and query: `chat_active_poll_interval`, `chat_inactive_poll_interval` — these controlled HTTP polling which no longer exists
- Chat Settings card now shows a "WebSocket (v3.0.0)" green badge and explanatory copy: *Messages are delivered in real time via WebSocket (Django Channels + Redis). HTTP message polling was removed in v3.0.0.*

**Files changed:** `src/view/admin_v2.py`, `src/view/chat/channel_chat.py`, `templates/admin_v2/dashboard.html`

---

#### @Mention Notifications & Clickable Mentions

In-app notifications for `@mentions` in chat, plus Discord-style clickable mention spans that open a profile card popup.

**Notification bell:**
- Added `chat_mention` to `Notification.NOTIFICATION_TYPES` (migration 0190)
- `send_channel_message` now creates a `Notification` record for each mentioned user (skips self-mentions), sets `link` to the channel URL, and invalidates the recipient's notification badge cache immediately
- Chat mention notifications appear in the bell dropdown with an `@` icon

**@mention toast:**
- WS broadcast payload now includes `mentioned_user_ids` (list of `user_id` strings)
- When a `message` event arrives and `currentUserId` is in `mentioned_user_ids`, a blue fade-in toast appears at the top of the page: *"Name mentioned you"* — auto-dismisses after 4 s

**Clickable @mention spans:**
- `renderMessageText()` now renders `@username` as a `<button>` with `data-username` and `onclick="handleMentionClick(this)"`
- `handleMentionClick` lazy-loads channel members (same `loadChannelMembers()` call used by autocomplete) and opens a full profile card popup via `openProfileModal(userId)`
- `channelMembers` array now populates a `memberByUsername` Map on load for O(1) username → user_id lookups

**Profile card popup:**
- Full profile modal added to `channel.html` — same fields as the directory popup (avatar, type badge, roll number, about, contact, academics, chapter info, current roles, role history, socials, house)
- House assignment edit UI intentionally omitted (read-only view only); house badge still shown
- `_renderProfile` logic refactored with `_pm`-prefixed helpers to avoid conflicts if directory.html code is ever present

**Files changed:** `src/models/notifications.py`, `src/migrations/0190_notification_chat_mention.py` (new), `src/view/chat/channel_chat.py`, `templates/chat/channel.html`, `templates/base.html`

**Deployment:**
```bash
python manage.py migrate   # migration 0190
```

---

#### Security Hardening — Admin v2 Rate Limiting & Env Config

Two red-severity findings from the automated security report (2026-06-05).

**Admin v2 rate limiting (`src/view/admin_v2.py`):**
- Added cache-based attempt counter per user (`admin_v2_attempts_{pk}`) with a 5-attempt ceiling and 15-minute lockout window
- Counter increments on each bad user-password or bad secret-key submission; clears on successful login
- Mirrors the pattern already used on the main login lockout

**ALLOWED_USER_IDS env var:**
- Replaced hardcoded `ALLOWED_USER_ID = '73'` with `ADMIN_V2_USER_IDS` env var (comma-separated, e.g. `73,81`)
- Falls back to legacy `ADMIN_V2_USER_ID` for backwards-compatibility during deploy
- Add `ADMIN_V2_USER_IDS=73` to the server's env; add future officers by appending their ID

**Files changed:** `src/view/admin_v2.py`, `.env`

---

#### Security Hardening — Chat Endpoint Fixes

Two yellow-severity findings from the automated security report (2026-06-05).

**Timestamp validation in `get_channel_messages` (`src/view/chat/channel_chat.py`):**
- The `since` query-param was passed directly to `created_at__gt=since`; Django coerces it but a malformed value would raise an unhandled exception
- Wrapped in `parse_datetime()` + try/except; returns 400 `Invalid since timestamp` on failure

**Chat send rate limiting:**
- Added per-user rate limit on `send_channel_message`: max 5 messages per 3-second window via cache key `chat_send_rate_{pk}`
- Returns 429 `You are sending messages too quickly` when exceeded

**Files changed:** `src/view/chat/channel_chat.py`

---

#### C&B Resolution Builder — QoL Improvements

Four usability improvements approved 2026-06-05.

**Save & Preview button:**
- Added to the edit resolution form alongside the existing "Edit Resolution" submit button
- On click, sets a hidden `save_and_preview` field and submits the form; the view detects this and redirects to the print view URL instead of the detail page
- Eliminates the save → navigate → open-print-view sequence

**Unsaved changes warning:**
- `beforeunload` event fires a browser confirm dialog if any field in the resolution form has been edited without saving
- Dirty flag is set on `input`/`change` events; cleared on form submit and on "Save & Preview"

**Amendment editing:**
- "Edit" button added to each amendment card (only shown for unapplied amendments)
- Clicking pre-fills the amendment modal with the existing section selection, proposed text, and scope note, and renders the diff from the stored original text snapshot
- Submitting re-uses the existing `add_amendment` view which already upserts by `(resolution, section)` — no new endpoint needed

**Section search in C&B drawer:**
- Live-filter `<input>` added at the top of the reference drawer, between the document tabs and the scrollable content
- Filters section rows within the active document tab by matching against identifier, title, and content text
- Matching articles auto-expand; non-matching articles are hidden entirely
- Search clears when switching document tabs

**Files changed:** `templates/cnb/resolution_form.html`, `src/view/officer/cnb.py`

---

### v2.29.0 - Daily Site Digest, Automated Housekeeping & Email Change Verification (2026-06-02) ✅ Deployed

Replaces the weekly system audit and daily honeypot digest with a single unified `send_daily_digest` task that runs nightly at 3:30 AM CST. Always sends (even on clean runs), shows all check results (OK and flagged), and includes a honeypot activity section. Adds four new housekeeping tasks: `prune_expired_login_lockouts` (daily), `expire_stale_ip_blacklist_entries` (daily), `prune_stale_push_subscriptions` (monthly), and `prune_old_auth_tokens` (monthly). Closes HIGH severity gap: email address changes now require confirmation to the new address before taking effect — first-time email set is still immediate. Run `migrate` and `setup_celery_schedules --reset` on deploy.

---

### v2.28.1 - Login 403 Bug Fix, Middleware Reorder & CSP Fix (2026-06-01)

Fixes mobile login 403s caused by `LoginRateLimitMiddleware` self-clearing its own lockout and returning a raw 403 page instead of redirecting. Moves `InputSanitizationMiddleware` to after `AuthenticationMiddleware` so the authenticated-user bypass actually works. Reduces geo lookup timeout from 4s to 2s. Vendors cropperjs locally to fix CSP violation on the profile page.

---

### v2.28.0 - 2FA Hardening, Auth Audit & API Foundation (2026-06-01)

Self-service 2FA recovery via email, "remember this device for 30 days" cookie, backup code acknowledgement tracking with a site-wide warning banner, auth view audit fixes (logout logging, rate limiting on password change and 2FA recovery, admin notification on recovery use), and a read-only REST API scaffolded on Django REST Framework as the 3.0.0 API foundation.

---

### v2.27.0 - Guide Accuracy Pass, Inline Poll Builder, My Work Page & Poll Privacy (2026-05-31)

Four independent areas: guide template corrections, polling UX improvements (inline creation + document search), page rename + enhanced vote display, and anonymous poll privacy hardening.

---

#### Guide Template Accuracy Pass

Fixed several inaccuracies in officer guide pages where the written descriptions had drifted from actual app behavior.

**Attendance guide (`officers/attendance.html`):**
- Section 1 replaced with "Where to Take Attendance" covering all three sync'd sources (Attendance Page, Minutes Editor, Vote Page Attendance tab)
- Section 2 "Attendance Statuses" — corrected colors: Late=yellow (was blue), Excused=blue (was yellow); added tip about excuse approval not overwriting Present/Late
- Section 4 "Reports" — replaced vague bullet list with accurate Attendance Dashboard description (per-member rates, monthly trends, at-risk members, history)

**Chapter Minutes guide (`officers/chapter_minutes.html`):**
- "Two main tabs" → corrected to "three collapsible sections: Attendance, Meeting Minutes, Publish"
- "Switch to the Attendance tab" → corrected to "Expand the Attendance section"
- Added Save Draft vs Save Attendance vs Publish distinction callout
- Added Section 4 "Markdown Formatting" with inline/block syntax reference grids

**Announcements guide (`officers/announcements.html`):**
- Added Section 2 "Linking Documents" — step-by-step instructions for the document picker
- Added Section 3 "Polls & Surveys" — how to create a poll, three question types as colored cards, results description (bar charts, non-respondents, CSV export)
- Renumbered remaining sections (Email Delivery → 4, Scheduling → 5, Targeting → 6, Engagement Stats → 7)

**Files changed:** `templates/guide/officers/attendance.html`, `templates/guide/officers/chapter_minutes.html`, `templates/guide/officers/announcements.html`

---

#### Handoff Docs Surfaced In-App

`docs/OFFICER_GUIDE.md` and `docs/HANDOFF_DEVELOPER.md` are now rendered as live pages inside the guide system. The markdown is rendered server-side with the `markdown` library (tables, fenced_code, toc extensions) and sanitized via `bleach`.

**New:**
- `guide_officer_handoff` view — renders `OFFICER_GUIDE.md`; linked from Officer Guides hub and General Help
- `guide_developer_handoff` view — renders `HANDOFF_DEVELOPER.md`; officer-gated; linked from General Help only
- `templates/guide/handoff.html` — shared template with breadcrumb, document header card, and table styles for handoff content
- `_render_markdown_doc()` helper in `guide.py` with extended allowed tag set for tables

**Changed:**
- `guide/index.html` — General Help card links to both docs; developer link gated with officer check; Officer Guides grid gains an "Officer & Admin Guide" card and gated "Developer Handoff Guide" card
- `guide/category_hub.html` (officer hub) — "Officer & Admin Guide" amber card added at the bottom

**Files changed:** `src/view/guide.py`, `src/urls.py`, `templates/guide/handoff.html` (new), `templates/guide/index.html`, `templates/guide/category_hub.html`

---

#### Document Search Bar on Announcement Forms

The "Attach Chapter Documents" accordion on both create and edit announcement forms was previously unsearchable. With many chapter documents, officers had to scroll through the full list to find what they wanted.

**Changed:**
- Search input added above the document list in both forms; filters by document title client-side on `oninput`
- "No documents match your search." empty-state message
- Search input cleared when the accordion is collapsed

**Files changed:** `templates/officer/create_announcement.html`, `templates/officer/edit_announcement.html`

---

#### Inline Poll Builder on Create Announcement

Previously a poll could only be added after an announcement was created, from the edit page. The poll builder is now available directly on the create announcement form as a collapsible "Add Poll / Survey (optional)" accordion.

Poll fields (title, description, is_open, is_anonymous, closes_at) and a full dynamic question/option builder are submitted alongside the announcement in a single POST. A `_save_poll_from_post()` helper was extracted to handle poll creation from POST data, shared between create and the redirect flow.

On successful creation (email or non-email path) the redirect now lands on the edit page rather than the manage list, so the poll is immediately visible and editable.

**New:**
- `_save_poll_from_post(post, announcement, user)` helper in `manage_announcements.py`
- Poll accordion section in `create_announcement.html` with full JS question/option builder (`togglePoll`, `updatePollBadge`, `addPollQuestion`, `removePollQuestion`, `addPollOption`, `onPollTypeChange`)

**Changed:**
- `create_announcement` view — calls `_save_poll_from_post` after saving linked documents if `poll_title` is present in POST
- Post-create redirect changed from `manage_announcements` to `edit_announcement` (both email and non-email paths)

**Files changed:** `src/view/officer/manage_announcements.py`, `templates/officer/create_announcement.html`

---

#### "My Legislation" Renamed to "My Work" + Polls Tab + Vote Display Improvements

The "My Legislation" page was renamed to "My Work" and expanded with a Polls tab showing all polls the user has created, plus improved vote result display for newer vote types.

**Changed:**
- Page title → "My Work"; subtitle updated; 4th stat card changed from "Active" to "Polls Created"
- Tab bar added ("Legislation" | "Polls & Surveys") with localStorage persistence for active tab
- Home page link labels updated in all three home templates (`home.html`, `home_modern.html`, `home_classic.html`)

**New (Polls tab):**
- Lists all polls created by the current user with: title, attached announcement, question count, response count, open/closed status, auto-close date
- Status strip (green = open, gray = closed); links to View Results, Edit Poll, View Announcement

**New (Legislation tab — vote display):**
- Chair Appointment type: amber "Chair Appt." badge; structured Role / Nominee info line; "Assigned" green badge when role is formally assigned post-vote
- Runoff votes: indigo "Runoff" badge; "Runoff from: [parent]" link; child runoff links shown under plurality results
- Plurality multi-select: "X selections per voter" shown when `plurality_votes_allowed > 1`
- Tie detection: "Tie — runoff may be required" shown when no winner and runoff is enabled
- Anonymous ballot noted in meta line

**Files changed:** `templates/legislation_history.html`, `src/view/view_legislation_history.py`, `templates/home.html`, `templates/home_modern.html`, `templates/home_classic.html`

---

#### Anonymous Poll Privacy Hardening

Three gaps in the anonymous poll system closed: the `is_anonymous` flag could be reversed after being set, individual respondent/non-respondent information leaked via process-of-elimination on polls with few responses, and the results page gave no useful information when a closed poll never reached the threshold.

**Changed:**

*Lock `is_anonymous` once set:*
- `announcement_poll_edit.html` — checkbox renders `disabled` with a grayed label and "(locked — cannot be removed once set)" note when editing an existing anonymous poll; hidden input carries the value so the form submits correctly
- `create_or_edit_poll` view — server-side guard: if `is_edit and poll.is_anonymous`, `is_anonymous` is forced to `True` regardless of POST contents

*Threshold-based respondent reveal (>2 votes):*
- `poll_results` view — for anonymous polls, both the respondent list and non-respondent list are withheld until `respondent_count > 2`; once met, `anon_respondent_users` (names only, no answers) and `non_respondents` are passed to context
- `announcement_poll_results.html` — purple notice card shown while below threshold (open: "X/3 so far"; closed with <3 votes: explains poll closed below threshold); above threshold: "Have Responded" section with purple name chips and "Individual answers are hidden" note; "Haven't Responded" unchanged

*Closed poll below threshold — partial non-respondent list:*
- When a closed anonymous poll has 1–2 responses, `floor(non_respondent_count / 2)` non-respondents are shown (enough for follow-up, not enough to identify voters by elimination)
- Non-respondent section header labeled "(partial list — some withheld to protect anonymity)" in this case

**Files changed:** `src/view/officer/announcement_polls.py`, `templates/officer/announcement_poll_edit.html`, `templates/officer/announcement_poll_results.html`

---

### v2.26.0 - Attendance System Overhaul (2026-05-31)

A full-session audit and rework of the attendance and voting systems, fixing several silent bugs that caused data to not be saved, pre-check states to display incorrectly, and minutes publish to snapshot stale attendance.

---

#### Attendance Dashboard Rewrite

Full visual rewrite of the officer attendance dashboard with better stats, color-coded trends, and at-risk highlighting.

**Changed:**
- Six stat cards (Rate, Events, Present, Late, Absent, Excused) with icons; Rate card color-coded green/yellow/red based on threshold
- Monthly trend bars color-coded green (≥80%), yellow (60–79%), red (<60%) with a legend
- At-risk section (hidden by default, revealed via JS when at-risk rows exist): lists members below threshold
- Member table sorted worst-first using `{% for item in member_stats reversed %}` so lowest performers appear at the top
- At-risk member rows get a red avatar tint; the section header/footer are stable HTML containers, not `forloop.first`/`forloop.last` (prior approach caused the wrapper to never render)
- Print button added via `onclick="window.print()"`

**Files changed:** `templates/officer/attendance_dashboard.html`

---

#### Voting: Allow Late Members to Vote

Previously, vote eligibility required `status='present'`. Members marked late were locked out of voting even if physically present.

**Changed:**
- `vote_view.py` — eligibility check updated from `present=True` filter to `status__in=['present', 'late']`

**Files changed:** `src/view/vote_view.py`

---

#### Vote Page: Quick Attendance Tab for Officers

Attendance could only be taken from the attendance page or via minutes — not from the vote page itself. Officers had to navigate away mid-meeting to mark someone present. Added a third "Attendance" tab to the officer panel on the vote page.

**New:**
- Officer panel on vote page gains an "Attendance" tab with a live present-count badge
- Per-member Present/Late/Absent toggle buttons; "All Present" and "Clear All" bulk actions; name search filter
- AJAX `markAttendance()` posts to a new `mark_attendance_quick` action handler; optimistic UI updates buttons and avatar color on click
- Shows current status for all members on load (not blank to start) — `members_attendance` and `attendance_present_count` added to context (officers only)
- Records write as `attendance_type='committee', committee=None` (vote-context attendance, no linked event required)

**Files changed:** `src/view/vote_view.py`, `templates/vote.html`

---

#### Minutes Attendance Sync Fix

Taking attendance in the minutes editor and saving did not reliably update the `Attendance` model records used by the vote page and attendance dashboard.

**Fixed:**
- `save_minutes_attendance` in `chapter_minutes.py` — added `created_at: now` to event-linked `update_or_create` defaults; added `else` branch for minutes with no linked event to write `attendance_type='committee', committee=None` records (previously no records were written at all for event-less minutes)
- Priority chain in `edit_chapter_minutes`: approved excuse now overrides a previously-saved `absent` status; chain is: approved-excuse-over-absent → saved status → event attendance map → approved excuse (any status) → pending

**Files changed:** `src/view/officer/chapter_minutes.py`

---

#### Minutes Publish Bug Fix

When the attendance panel was edited after the initial "Save Attendance" click, changes were tracked only in the in-memory JS array. Clicking "Publish" submitted the form without re-persisting, so the published PDF captured the old attendance snapshot.

**Fixed:**
- Extracted `_persistAttendance()` as a raw async fetch helper used by both the "Save Attendance" button and the new publish interceptor
- `saveAttendance()` now delegates to `_persistAttendance()` internally
- Removed `onclick="return confirm(...)"` from publish button; replaced with a form `submit` event listener that: (1) prevents default, (2) prompts for confirmation, (3) calls `_persistAttendance()`, then (4) calls `this.submit()` — attendance is always current at publish time
- Publish button disabled and relabeled ("Saving attendance… → Publishing…") while async work runs; re-enabled with an alert on failure

**Applies to:** Chapter minutes and committee minutes (both use `officer/chapter_minutes_editor.html`)

**Files changed:** `templates/officer/chapter_minutes_editor.html`

---

#### Committee Minutes: Write Attendance Records

The committee minutes save handler stored attendance in the `CommitteeMinutes` JSON field but never wrote `Attendance` model records. Committee attendance was invisible to any view that queried the `Attendance` table.

**Fixed:**
- `save_committee_minutes_attendance` now loops the attendance list after saving minutes and calls `Attendance.objects.update_or_create` for each non-pending member, writing `attendance_type='committee'` records against the correct committee

**Files changed:** `src/view/committee/committee_minutes_editor.py`

---

#### Attendance View: Silent `present=True` Reset Bug

The attendance toggle view set `present=True` or `present=False` in `update_or_create` defaults. Because `Attendance.save()` overrides `self.present` from `self.status`, and `status` defaulted to `'pending'`, the `present` flag was always silently reset to `False` after every toggle. Attendance appeared to save (HTTP 200) but no one was ever marked present in the DB.

**Fixed:**
- POST handler now sets `status='present'` / `status='absent'` directly in defaults instead of using the legacy `present` boolean
- Added `marked_by` and `marked_at` to the written record
- GET handler now queries `today_present_ids` (a set of `user_id` values for active-today attendance) and passes it to context
- Template pre-check updated from `{% if user.attendance_set.last.present and ... %}` to `{% if user.user_id in today_present_ids %}` — eliminates stale `.last` lookups

**Files changed:** `src/view/officer/attendance.py`, `templates/attendance.html`

---

#### Bug Fixes

| Area | Fix |
|------|-----|
| `AttendanceExcuse.approve()` | Did not set `created_at` in `get_or_create` defaults — new records got `auto_now_add` timestamp, but `created_at` was ambiguous as a lookup/default field; now explicitly set to `now` |
| `AttendanceExcuse.approve()` | Previous condition `if attendance.status != 'excused'` would overwrite `present` or `late` when an officer approved an excuse for a member who had actually attended; changed to `not in ('present', 'late', 'excused')` |
| `submit_excuse.py` | Deadline-passed and finalized-attendance errors both showed a generic message; now shows distinct messages for each case |

**Files changed:** `src/models/events.py`, `src/view/submit_excuse.py`

---

## Changelog Format (v2.24+)

Starting with v2.24.0, all changes are documented here. Version numbering:
- **x.Y.0** — new feature or significant rework
- **x.y.Z** — bug fix or small enhancement to an existing feature

Bug fixes caught between releases (i.e. fixed in the same session they were found, before a new version is tagged) are documented in the **Bug Fixes** section below rather than getting their own version entry. This keeps the version list meaningful without losing the fix history.

### Bug Fixes (undocumented between versions)

Fixes caught and resolved within the same session as their discovery — too small for a version bump, too important to leave unrecorded.

| Date | Area | Fix |
|------|------|-----|
| 2026-05-30 | Committee | `ActivityLog` missing from `committee_index.py` imports — caused silent 500 after any archive/ad-hoc save (DB write succeeded but error response returned) |
| 2026-05-30 | Committee | `TemplateSyntaxError` on `/committees/` from `item.roles.split:','` — Django templates can't call Python methods with arguments; fixed by passing roles as a list from the view |
| 2026-05-30 | Committee | Recent Documents / Recent Votes grids overflow on mobile — fixed with `min-w-0` on grid children |
| 2026-05-30 | Login-As | `login_as_view` session keys wiped by Django's `session.flush()` — fixed by writing keys after `login()`, not before |
| 2026-05-30 | Login-As | Impersonation URLs unreachable under `admin/` prefix (Django admin catches all `admin/*`) — moved to `staff/` prefix |
| 2026-05-30 | Login-As | `<int:user_id>` in URL pattern but `user_id` is a `CharField` PK — changed to `<str:user_id>` |
| 2026-05-30 | Migration | Migration 0160 `RunPython` crashed test DB build — `ParliamentUser.objects.all()` selected columns not yet added at that migration point; fixed with `.only('pk', 'name', 'user_id', 'password')` |
| 2026-05-30 | Feature Flags | `seed_feature_flags.py` used `page_name` key throughout page toggles but the model field is `url_name` — would raise `TypeError` on any fresh seed run |
| 2026-05-30 | Feature Flags | `announcements` feature flag never seeded — view used `@require_feature_flag('announcements')` but the flag row didn't exist, making it untoggleable from admin |

---

### v2.24.4 - Committee Index Enhancements & Bug Fixes (2026-05-30)

Expands committee index cards with more detail, fixes a template crash, a 500 on committee save, and a mobile layout overflow — plus adds the login-as test suite.

**Changed:**
- Committee index cards (`/committees/`) now show: code badge, member/chair/advisor counts with icons, role badges per card, and a three-button footer (Open / Chat / Docs)
- Committee index view now passes `roles` as a list instead of a comma-joined string; template iterates with `{% for role in item.roles %}` — fixes `TemplateSyntaxError` from `item.roles.split:','`
- Committee index view now annotates each committee dict with `member_count`, `chair_count`, `advisor_count`
- Recent Documents and Recent Votes grids on committee home now use `min-w-0` on children to prevent horizontal overflow on mobile
- `committee_index.py` imports `ActivityLog` (was missing — caused a `NameError` on any save/archive/ad-hoc action, returning a 500 while the DB change had already committed)

**Tests:**
- `src/test_login_as.py` (11 tests) added — covers non-staff blocked, admin impersonation, session key storage, context processor True/False, return-to-original session clear, return without session, 2FA bypassed during impersonation, 2FA still enforced normally, impersonation start/end logging

**Files changed:** `src/view/committee/committee_index.py`, `templates/committee/committee_index.html`, `templates/committee/committee_home.html`, `src/test_login_as.py`

---

### v2.24.3 - Login-As-User Reworks (2026-05-30)

Improves admin impersonation with session tracking, a persistent banner, a one-click return button, and 2FA bypass.

**New:**
- `return_to_original_user` view (`/admin/return-to-original/`) — logs admin back in as themselves and clears impersonation state
- `impersonation` context processor — exposes `is_impersonating` and `impersonation_original_name` to all templates
- Impersonation banner in `base.html` — amber bar shown on every page during an impersonation session, with the target user's name, the admin's name, and a "Return to [admin]" button
- 2FA bypass in `Enforce2FAMiddleware` — sessions with `_impersonating_original_user_id` skip 2FA enforcement entirely so admins can't get stuck at the target user's 2FA screen

**Changed:**
- `login_as_view` now stores original admin's user_id and display name in the session after `login()` (post-flush), and logs both to the security logger and ActivityLog
- `return_to_original_user` logs the impersonation end to both loggers and clears all impersonation session keys

**Files changed:** `src/view/login_as_view.py`, `src/middleware/two_factor.py`, `src/context_processors.py`, `Parliament/settings_postgres.py`, `src/urls.py`, `templates/base.html`

---

### v2.24.2 - Committee Home Redesign (2026-05-30)

Merged the committee detail page's functionality into the home page and brought the UI in line with the rest of the site.

**Changed:**
- `committee_home` view now enforces `@login_required` and performs the same access check as the detail view (members, chairs, advisors, VP, admin, committee admin)
- `committee_home` view now builds full context: `permissions` object, eligible member/chair/advisor/voter lists for the management modal, `is_member`, `is_voting_member`, `is_committee_admin`, Kai reports (for Kai committee chairs/admins), and slating periods (for the slating committee)
- `committee_home.html` header replaced: gradient removed, now a clean white card matching the site's card-based UI — coat of arms, name, code, admin info, status badges (Inactive/Archived/Ad-hoc), user role pills, Chat and Chat Guests buttons
- Member roster added to home page — Chairs, Advisors, Voting Members, Members each in their own card with inline Remove buttons and role-appropriate Add buttons (chair/advisor/voter/member), gated by `can_manage` and hidden when committee is archived
- Actions section redesigned — now uses subtle tinted tiles instead of solid colored blocks; Chair-only actions (Upload, Create Vote, Push to Chapter, Minutes, Attendance, Manage Members) gated behind `can_manage`; member-accessible actions (View Documents, Committee Voting) gated by permissions
- Add Member modal ported from `detail.html` to `committee_home.html` — same filtered eligible lists and `openManageModal(roleType)` JS
- Kai Reports section ported from `detail.html` to home page
- Slating section ported from `detail.html` to home page
- Stats grid simplified — removed per-card SVG icons, cleaner compact layout

**Files changed:** `src/view/committee/committee_home.py`, `templates/committee/committee_home.html`

---

### v2.24.1 - Multiple Academics & Initiation Chapter Role Numbers (2026-05-29)

Extends the member profile system to support multiple majors/minors/concentrations and adds a per-chapter role number field to initiation chapters.

**New Model Fields (migration 0180):**
- `majors`, `minors`, `concentrations` — JSONField lists replacing the old `major`, `minor`, `concentration` CharFields; existing data migrated automatically
- `initiation_chapters` entries now accept an optional `role_number` key (no migration needed — JSONField is schemaless)

**Changed:**
- Profile and admin edit pages now show add/delete UI for each academic field (same pattern as custom socials and initiation chapters)
- Initiation chapter add forms now include an optional Role # field; role number displayed inline in both profile page listing and modal popups
- `profile_card_json` now returns `academics` as `{majors: [...], minors: [...], concentrations: [...]}` instead of flat strings
- Directory and house map modals updated to render list-valued academics
- `refresh_from_db()` added to profile view before render to ensure form pre-population always reflects saved state

**Files changed:** `src/models.py`, `src/migrations/0180_parliamentuser_academic_fields.py`, `src/view/profile_view.py`, `src/view/admin_v2.py`, `src/view/profile_card.py`, `templates/profile.html`, `templates/admin_v2/edit_user_profile.html`, `templates/directory.html`, `templates/house_map.html`

---

### v2.24.0 - Extended Member Profiles, House System & House Map (2026-05-29)

Full overhaul of the member profile system adding rich optional profile data, a big/little family tree, a house assignment system with auto-propagation, a house map page, and a profile popup modal in the directory.

**New Models / Fields (migrations 0176–0179):**
- `about_me`, `major`, `minor`, `concentration` — bio and academics (later migrated to `majors`/`minors`/`concentrations` JSONFields in v2.24.1)
- `big_brother` — self-referential FK; `little_brothers` is the reverse relation
- `pledge_class`, `pledge_class_greek` — pledge class name and Greek letter name (e.g. "Alpha Beta" → αβ); "Founder" gets a gold gradient badge
- `graduation_semester`, `graduation_year`
- `instagram`, `twitter`, `linkedin`, `snapchat`, `facebook`, `other_email` — standard social handles
- `custom_socials` — JSONField list of `{"platform", "handle"}` for non-standard platforms (Signal, WhatsApp, etc.)
- `house` — CharField with fixed 8-house choices (Smith, Duncan, Knox, Marshall, Linton, Hardin, Ryan, Gordon); not user-editable from UI
- `initiation_chapters` — JSONField list of `{"school", "chapter", "role_number"?}` dicts; defaults to display "Alpha Mu (αμ) — Samford University" when empty; supports dual-chapter membership; `role_number` added in v2.24.1
- `RoleHistory` model — tracks positions held with start/end semesters; many-to-one with `ParliamentUser`

**Directory Profile Modal:**
- Clicking any member name or avatar in the directory opens a slide-up modal fetching from `GET /directory/<user_id>/card/`
- Shows: profile picture (or initial avatar), member type badge, roll number + ID, bio, contact info, academics, chapter info (pledge class with Greek glyphs + Founder badge, graduation, big/little, initiation chapters), current roles, role history, socials + custom socials, house
- Pledge viewers cannot see roll number or user ID for any member
- Removed members show a red "Removed" badge instead of their member type

**House System:**
- House assignment restricted to Officers, Admins, and Chairs whose role name contains "historian"
- `POST /officers/members/<user_id>/set-house/` — JSON endpoint to set/clear house
- Auto-propagation: when a big brother has a house, setting that big on a houseless user cascades the house down through their houseless descendants (BFS); stops at anyone who already has a house set
- House setter UI appears in the directory profile modal for authorized users

**House Map (`/house-map/`):**
- 8 house cards in a 2-column grid, each showing active/total member counts
- Family trees rendered client-side with CSS connector lines; multiple separate family lines within one house are separated by a dashed divider
- Tree includes houseless littles (implied same house) and stops at littles assigned to a different house
- Status dots: green = Active, yellow = Pledge, gray = Inactive/Alumni, red = Removed
- Removed members show "Removed" badge in tree instead of member type
- Clicking any name opens the same profile modal
- "House Map" button added to the directory header

**Profile Self-Edit (`/profile/`):**
- New "Public Profile" card: bio, academics, chapter section (pledge class, pledge class Greek name, big brother select including removed members, little brothers read-only), graduation, standard socials, other email
- Custom Social Links section: add/remove non-standard platform handles
- Initiation Chapters section: add/remove `{school, chapter}` entries; empty shows Samford default note
- Role History section: add/remove past positions with start/end semesters

**Admin Edit Profile (`/admin-v2/users/<id>/edit-profile/`):**
- New page for officers/admins to edit any member's full profile
- Core section: name, preferred name, member type/status, email, phone, roll number, house dropdown
- Public Profile section: all extended fields including big brother (with house propagation on save)
- Dedicated sections for Role History, Custom Socials, and Initiation Chapters with add/remove

**Bug Fixes:**
- `preferred_name` `IntegrityError` — admin edit was saving `None` to a non-nullable `CharField`; fixed to store `''`
- House map "Loading…" stuck — `json_script` element was placed after the `<script>` block that reads it; moved before
- Removed member modal badge — was showing member type (Member/Officer/etc.) instead of "Removed"; fixed in both directory and house map modals

**Files added:**
- `src/view/profile_card.py` — `GET /directory/<user_id>/card/` JSON endpoint
- `src/view/officer/set_member_house.py` — `POST /officers/members/<user_id>/set-house/`
- `src/view/house_map.py` — house map view with per-house tree building
- `src/house_utils.py` — `propagate_house()` and `inherit_house_from_big()` utilities
- `templates/house_map.html` — house map page with profile modal
- `templates/admin_v2/edit_user_profile.html` — admin full-profile editor
- `src/migrations/0176_parliamentuser_profile_fields.py`
- `src/migrations/0177_parliamentuser_house_custom_socials.py`
- `src/migrations/0178_parliamentuser_pledge_class_greek.py`
- `src/migrations/0179_parliamentuser_initiation_chapters.py`

**Files changed:**
- `src/models.py` — all new profile fields + `RoleHistory` model + `HOUSE_CHOICES`
- `src/view/profile_view.py` — handlers for all new profile sections
- `src/view/admin_v2.py` — `edit_user_profile` view + all action handlers; `preferred_name` null fix
- `src/view/directory.py` — `can_set_house` + `house_choices` context; profile card endpoint
- `src/templatetags/custom_filters.py` — `jsonify` filter added
- `templates/directory.html` — profile modal HTML + JS (openProfileModal, _renderProfile, submitHouseChange); House Map button; Removed badge fix
- `templates/profile.html` — all new self-edit sections
- `src/urls.py` — profile card, house map, set-house, edit-profile URLs

### v2.23.0 - Chair Appointment Votes (05-29-2026)

- Officers can create appointment votes directly from the legislation page via a new "Chair Appointment" tab
- Appointment votes support all three vote modes (percentage, piecewise, plurality) — plurality allows multiple candidates as options
- When an appointment vote passes, the author is redirected to `/legislation/<id>/assign/` to formally assign the role
- Assignment promotes the member's `member_type` to `Chair` (if currently `Member`) and adds the role via `member.roles.add()`
- `appointment_assigned` flag tracks completion; unassigned passed appointments show a persistent callout on the passed legislation page
- Appointment votes display an "Appointment" badge + role name pill on the vote feed
- 4 new fields on `Legislation` model (`legislation_type`, `appointment_role`, `appointment_member`, `appointment_assigned`); migration `0174`
- 21 new tests: `CreateAppointmentTests`, `AssignAppointmentGetTests`, `AssignAppointmentPostTests`

**Files added:**
- `src/migrations/0174_legislation_appointment_fields.py`
- `src/view/assign_appointment.py`
- `templates/assign_appointment.html`

**Files changed:**
- `src/models.py` — 4 new fields on Legislation
- `src/view/upload_legislation.py` — appointment creation path
- `src/view/edit_legislation.py` — redirect to assign_appointment on passed appointment
- `src/view/vote_view.py` — appointment_roles/members in context
- `templates/vote.html` — appointment tab + form + badge
- `templates/passed_legislation.html` — unassigned appointment callout
- `src/urls.py` — assign_appointment URL
- `src/test_pillar3.py` — 3 new test classes

**Deployment:** Requires migration (`python manage.py migrate`). `collectstatic` + Cloudflare purge required.

---

### v2.22.0 - Officer/Role Transition Tools (05-29-2026)

- Added dedicated role handoff page at `/officers/transitions/` — shows all roles with current holders and a Transfer button per row
- Transfer modal performs an atomic swap: removes role from outgoing holder, assigns to incoming, and optionally updates member_type for both parties in one logged operation
- Auto-grants `is_admin` when a `grants_admin` role is transferred to a non-admin member
- Auto-clears existing holder on `one_per_chapter` roles when no explicit outgoing is specified
- `demote_outgoing` flag: reverts outgoing member to Member type only if they hold no remaining qualifying roles (Officer type preserved if another `grants_admin` role remains)
- Full `ActivityLog` entry with action type `transfer_role` and complete changes list
- 14 new tests in `src/test_pillar3.py` covering auth, GET render, swap, type cascade, grants_admin, demotion guard, and validation errors

**Files added:**
- `src/view/officer/transitions.py`
- `templates/officer/role_transitions.html`

**Files changed:**
- `src/urls.py` — two new URL patterns + import
- `src/test_pillar3.py` — 3 new test classes (TransitionAuthTests, TransitionListTests, TransferRoleTests)

**Deployment:** No migrations. `collectstatic` + Cloudflare purge required.

---

### v2.21.0 - Member Directory Enhancements (05-29-2026)

- Added client-side type filter pills (Officers, Chairs, Members, Pledges, Advisors, Alumni) with active/inactive styling
- Added sort dropdown (A→Z, Z→A, Roll #) — sort=roll and sort=name_desc handled server-side; dropdown reloads page via URL param
- Added `data-type` attribute to all six member card groups so the JS filter can target them by type
- Added `filter_type` and `sort` keys to view context so the template can pre-select active states
- Added `src/test_pillar3.py` covering: auth requirement, member grouping, alumni toggle, sort correctness, filter_type context, export defaults, and export column selection

**Files changed:**
- `templates/directory.html` — filter pills, sort dropdown, `data-type` on all card types, JS filter/sort logic, `.filter-pill` CSS
- `src/view/directory.py` — sort logic, `sort` + `filter_type` context keys *(already landed this session)*
- `src/test_pillar3.py` — new Pillar 3 test file

**Deployment:** Static files changed — `collectstatic` + Cloudflare cache purge required. No migrations.

---

### v2.20.0 - Push Notification Admin Management (05-28-2026)
Adds admin-v2 tools for managing push subscriptions and a member-facing reconfigure button. Admins can view all registered devices, delete individual subscriptions, toggle push on/off globally or per notification type, and mass-clear all subscriptions from the dashboard — no database access required. Members can now resync their push subscription in one click from the preferences page, which handles rotated endpoints, expired subscriptions, or post-admin-clear scenarios.

**Deployment Status:** ✅ Deployed

**New Files:**
- **`templates/admin_v2/push_subscriptions.html`** — Table listing every registered device with user, user agent, subscribed date, last used date, truncated endpoint, and individual delete buttons.

**Modified Files:**
- **`src/view/admin_v2.py`** — Added `PushSubscription` import; dashboard seeds 5 push feature flags on first load (`push_notifications_enabled` master + 4 per-type); adds push stats and flags to context; new `push_subscriptions_list` and `delete_push_subscription` views.
- **`src/urls.py`** — Added `admin-v2/push/subscriptions/` and `admin-v2/push/subscriptions/<id>/delete/`.
- **`templates/admin_v2/dashboard.html`** — New Push Notifications card: subscriber/device stats, per-flag ON/OFF toggles, "View All Subscriptions" link, "Clear All" danger zone.
- **`templates/preferences.html`** — "Reconfigure" button next to "Disable" in the push-enabled state; re-subscribes the current device fresh, handling rotated endpoints and cleared subscriptions.

**Deployment steps:**
1. `git pull origin main`
2. `python manage.py collectstatic --noinput`
3. `systemctl restart parliament`
4. Purge Cloudflare cache
5. Load admin-v2 dashboard once — push flags seeded automatically on first page load

> No migration required.

---

### v2.19.0 - PWA & Web Push Notifications (05-27-2026)
Adds Progressive Web App support and Web Push notifications. Members can add Parliament to their home screen for a native-app feel. Every in-app notification now fires a push to all subscribed devices simultaneously.

**Deployment Status:** ✅ Deployed

**New Files:**
- **`static/manifest.json`** — PWA manifest. Enables "Add to Home Screen" on iOS/Android. Sets app name, icon (coat of arms), theme color (#1d4ed8), standalone display mode, and start URL (/home/).
- **`static/js/service-worker.js`** — Handles incoming `push` events and `notificationclick`. On tap: focuses an existing Parliament tab if one is open, otherwise opens a new one. Uses tag-based deduplication to prevent notification stacking.
- **`src/view/push_notifications.py`** — Three endpoints: `GET /service-worker.js` (serves SW with `Service-Worker-Allowed: /` header so it controls the full site), `POST /push/subscribe/`, `POST /push/unsubscribe/`.
- **`src/migrations/0172_push_subscription.py`** — Migration for PushSubscription model.

**Modified Files:**
- **`templates/base.html`** — Added `<link rel="manifest">` and `<meta name="theme-color">`. Registers service worker at `/service-worker.js` for authenticated users only.
- **`src/models.py`** — Added `PushSubscription` model: stores endpoint, p256dh key, auth secret, user agent, timestamps. One user → many devices. `as_subscription_info()` returns the dict pywebpush expects.
- **`src/tasks.py`** — Added `send_push_notification` Celery task. VAPID-signed via pywebpush. Skips gracefully if VAPID keys not configured. Auto-deletes subscriptions that return 410 Gone (expired).
- **`src/notification_service.py`** — Added `_dispatch_push_notifications()` helper. Called in both `notify_all_active_members()` and `notify_users()` after `bulk_create` — every in-app notification now triggers a push automatically.
- **`src/urls.py`** — Wired `/service-worker.js`, `/push/subscribe/`, `/push/unsubscribe/`.
- **`Parliament/settings_postgres.py`** — Added VAPID settings block. Keys stored as base64url in `.env`, reconstructed to PEM at runtime.
- **`requirements.txt`** — Added `pywebpush==2.3.0`.

**Deployment steps:**
1. `git pull origin main`
2. `pip install -r requirements.txt`
3. `python manage.py migrate`
4. Generate VAPID keys on server (see DEPLOYMENT_NOTES.md), add to `.env`
5. Add `REDIS_URL=redis://127.0.0.1:6379/0` to `.env` if not present
6. `systemctl start redis` and `systemctl enable redis`
7. `python manage.py collectstatic --noinput`
8. `systemctl restart parliament`
9. Copy and enable `parliament-worker.service` and `parliament-beat.service`
10. Purge Cloudflare cache

---

### v2.18.0 - Celery + Async Infrastructure & Live Vote Tallies (05-27-2026)
Introduces Celery + django-celery-beat as the async task queue and periodic scheduler. Emails no longer block gunicorn threads. Votes open and close on schedule without a page load. Scheduled announcements fire on time via Beat. Vote tallies now update live without a page reload.

**Deployment Status:** ✅ Deployed

**Type:** Infrastructure / Feature

**Migration required:** Yes — `python manage.py migrate` (django-celery-beat creates its own tables)

**New server steps required:**
1. `pip install -r requirements.txt` (adds `celery`, `django-celery-beat`)
2. `python manage.py migrate` (django-celery-beat tables)
3. `python manage.py setup_celery_schedules` (seeds Beat periodic task schedules)
4. Copy `parliament-worker.service` and `parliament-beat.service` to `/etc/systemd/system/`
5. `systemctl daemon-reload && systemctl enable --now parliament-worker parliament-beat`
6. Purge Cloudflare cache (static files unchanged, but do it anyway to be safe)

**Changes:**

- **`Parliament/celery.py`** — Celery application config. Auto-discovers tasks from all installed apps. Settings namespace is `CELERY_` (reads from Django settings).
- **`Parliament/__init__.py`** — Imports `celery_app` on Django startup so `@shared_task` decorators resolve correctly.
- **`Parliament/settings_postgres.py`** — Added full Celery configuration block: broker/result backend from `REDIS_URL`, JSON serialization, `CELERY_TASK_ACKS_LATE=True`, `DatabaseScheduler`. Added `django_celery_beat` to `INSTALLED_APPS`. Falls back to `memory://` broker if `REDIS_URL` is not set (dev/test only).
- **`src/tasks.py`** — Central task file with four groups:
  - *Email tasks:* `send_announcement_email`, `send_security_alert_task`, `send_pledge_welcome_task` — async wrappers so email sends don't add latency to request/response cycles. Each retries up to 3 times on failure.
  - *Vote tasks:* `auto_open_close_chapter_votes`, `auto_open_close_committee_votes`, `auto_open_close_slating_votes` — run every minute via Beat, replacing the on-page-load auto-close in `vote_view.py` and `committee/vote.py`. Votes now open and close on their scheduled time regardless of whether anyone loads the page.
  - *Announcement task:* `publish_scheduled_announcements` — runs every 5 minutes, finds announcements with `publish_at <= now` and `send_email_on_publish=True`, atomically claims each row, then queues `send_announcement_email`. Uses `select_for_update(skip_locked=True)` to prevent duplicate sends if Beat fires twice.
  - *Housekeeping:* `cleanup_expired_sessions` (daily 3 AM), `send_daily_honeypot_digest` (daily 7 AM).
- **`src/management/commands/setup_celery_schedules.py`** — Seeds `PeriodicTask` records for all default schedules on first deploy. Safe to re-run (uses `get_or_create`). Pass `--reset` to force-recreate schedules after renaming tasks.
- **`parliament-worker.service`** — systemd unit for the Celery worker. Single-concurrency (predictable memory on the VPS). Restarts on failure. Logs to `/var/www/Parliament-New/logs/celery-worker.log`.
- **`parliament-beat.service`** — systemd unit for Celery Beat. Uses `DatabaseScheduler` so schedules can be paused/adjusted from the database without restarting the process. Logs to `/var/www/Parliament-New/logs/celery-beat.log`.
- **`requirements.txt`** — Added `celery==5.4.0`, `django-celery-beat==2.7.0`.
- **Live vote tallies** — `GET /vote/tally/` JSON endpoint returns current vote counts for all open legislation. On the vote page, a self-contained IIFE polls every 15 seconds (only when the tab is visible via Page Visibility API) and updates each count in-place with a brief opacity flash on change. If the server reports a vote just closed while the user was watching, the page reloads automatically so the closed-state UI renders correctly. Polling only starts if at least one tally container is present (i.e., the user is an author of open legislation). Files: `src/view/vote_view.py` (`vote_tally_json`), `src/urls.py`, `templates/vote.html`.

---

### v2.17.0 - Pledge Onboarding, Security Hardening & Admin Dashboard Fixes (05-27-2026)
Adds pledge welcome emails, pledge activity logging, slating dashboard UI refresh, FK auto-discovery for pledge initiation, quarantine session enforcement, and fixes several admin-v2 dashboard bugs.

**Deployment Status:** ✅ Deployed

**Type:** Feature / Improvement / Bug Fix

**Migration required:** None

**Changes:**

- **Pledge welcome email** — When a pledge account is created with an email address, a welcome email is automatically sent with their username, initial password, site link, and an overview of what they have access to (announcements, calendar, documents, service hours, profile). Uses the existing email infrastructure with email-flagging on delivery failure.
- **Pledge activity logging** — Each pledge login is now recorded as a `pledge_login` activity log entry. The first password change (forced) is recorded as `pledge_password_changed`. Both are filterable by `action_type` in the admin log. Two new `ACTION_TYPES` added to `ActivityLog`: `pledge_login` and `pledge_password_changed`.
- **Slating dashboard UI refresh** — Updated `dashboard.html` to match the setup page's UI style: compact sidebar nav (no icons), consistent `px-5` padding, `space-y-5` spacing, `+ New` header link on the admin card, and removed the generic info card.
- **Pledge initiation FK auto-discovery** — Replaced the 80+ entry hardcoded `related_tables` list in `initiate_pledges()` with Django ORM `_meta.get_fields()` auto-discovery. All reverse FK and OneToOne relations are now updated automatically, so new models are covered without manual maintenance. M2M relations (roles, co-authored legislation) are handled explicitly. A small `extra_tables` list covers the one non-ORM table (`calendar_subscriptions`). Steps 1–3 (raw SQL PK copy) and the cascade safety check remain unchanged.
- **Middleware logging improvement** — `InputSanitizationMiddleware` now logs the matched regex pattern alongside the attack type, making false positive diagnosis significantly faster.
- **Quarantine session enforcement** — New `QuarantineEnforcementMiddleware` added to `security.py` and wired into `settings_postgres.py`. A quarantined user who is already logged in is now immediately logged out on their next request (rather than being allowed to continue browsing until session expiry). They are redirected to `/login/?quarantined=1`, which displays an explanatory error message. Login already blocks quarantined users from re-entry, so the quarantine is fully enforced end-to-end.
- **Admin-v2 dashboard bugs fixed** — Three bugs repaired: (1) duplicate `class` attributes on `.card-body` divs (HTML only reads the first `class` attribute, so `p-4`/`space-y-2` padding/spacing were silently dropped); (2) duplicate `class` attributes on all chevron SVGs (`chevron-icon` was in the second attribute, making `querySelector('.chevron-icon')` return null — chevrons never rotated); (3) all six main cards defaulted to `data-expanded="false"`, causing them to load collapsed.
- **Admin-v2 lockdown banner wired** — `admin_v2_dashboard` view now fetches `SystemLockdown.get_instance()` and passes `lockdown_active` to the template. The emergency lockdown banner at the top of the dashboard was previously always hidden because the variable was never in context.
- **Admin-v2 card state persistence** — Card expand/collapse state is now stored in `localStorage` keyed by `data-card-id`. State survives page reloads and navigation. Falls back to the HTML `data-expanded` default if no stored state exists.

---

### v2.16.9 - Slating Results Bug Fixes (05-26-2026)
Fixes several bugs across the slating results flow: transition officer crash, dark mode home page banner, individual vote summary display, and election results document not saving to chapter documents.

**Deployment Status:** ✅ Deployed

**Type:** Bug Fix

**Migration required:** None

**Changes:**

- **Transition officers `FieldError` fixed** — `transition.py` was calling `.order_by('last_name', 'first_name')` on `ParliamentUser`, which uses a single `name` field. Changed to `.order_by('name')`.
- **Dark mode slating banner fixed** — Dark gradient classes (`dark:from-purple-900/20`, etc.) were absent from the compiled `tailwind.css` bundle. Rebuilt CSS with the Tailwind CLI so dark mode gradients render correctly on the home page slating card.
- **Individual vote summary on results page** — The results page was showing "Slate vote failed" (0% approval) for individual-mode elections because it always rendered the slate vote section. Now branches on `period.vote_type`: individual mode shows an "X of Y Position(s) Passed" summary grid with a green/amber status banner; slate mode shows the original approve/reject/abstain breakdown.
- **Individual vote button on home page** — The "Vote Now" button in the home page slating banner now routes to `slating_vote_individual` for individual-mode elections instead of always pointing to `slating_vote`.
- **"Published on" date on results page** — The published banner now shows the publish date and time when `results_publish_at` is set, with a fallback text when it isn't.
- **Election results document saved to chapter documents** — `_save_results_to_documents` was filing under `period.slating_committee` instead of the chapter committee, and the write-in candidate path crashed (`application.applicant.name` on a `None` application). Fixed to use `Committee.objects.get(is_chapter_committee=True)` and `candidate.candidate_name`. Added update-in-place logic so republishing overwrites the existing document instead of creating duplicates.
- **`change_period_status` now saves results document** — Publishing via the period setup page bypassed `_save_results_to_documents` entirely. Now `change_period_status` calls it when transitioning to `results_published`, so the document saves regardless of which publish path is used.
- **Unpublish/republish redirects to results page** — `change_period_status` now redirects to `slating_results` for any results-related status transition, keeping the user in context instead of dropping them on the setup page.

---

### v2.16.8 - Individual Position Voting Overhaul & Per-Position Abstain Toggle (05-26-2026)
Rewrote individual position voting with a card-per-position UI, added per-position abstain control, and added individual vote breakdowns to the results page.

**Deployment Status:** ✅ Deployed

**Type:** Feature

**Migration required:** Yes — `0170_slatingposition_allow_abstain`

**Changes:**

- **Per-position abstain toggle** — `SlatingPosition` now has an `allow_abstain` boolean field (default `True`). Chairs can enable/disable abstain per position in the positions manager. Migration `0170` adds the column.
- **Individual voting UI rewrite** — `vote_individual.html` replaced with a card-per-position layout. Each position shows Approve / Reject / (Abstain if enabled) radio buttons and a "Voted" badge once submitted. A single password field sits at the bottom. JS guards the submit button if any position is missing a selection.
- **`individual_vote` view rewrite** — Now builds a `rows` list (all primary candidates) with `voted` and `allow_abstain` per row. Validates every unvoted position has a valid selection before recording any ballot. Enforces `allow_abstain` server-side. Records one `SlatingBallot(vote_type='individual', position=...)` and one `SlatingVote(slate_candidate=...)` per position.
- **Individual vote results on results page** — New "Individual Position Results" card on `results.html` shows per-position approve/reject/abstain counts, a progress bar, and a pass/fail indicator (uses `required_approval_percentage`). Uses the `|get_item` custom template filter to look up per-candidate vote counts from `individual_results`.
- **`allow_abstain` in position forms** — Add and edit position forms in `positions.html` include the abstain checkbox. Position JSON endpoint returns `allow_abstain` for the edit modal.

---

### v2.16.7 - Edit Approved Slate During Voting (05-26-2026)
Chairs and admins can now replace any candidate on the approved slate during voting.

**Deployment Status:** ✅ Deployed

**Type:** Feature

**Migration required:** None

**Changes:**

- **New `edit_approved_slate` view** — `/slating/period/<id>/slate/edit/` lets any chair-level user replace any candidate on the approved slate during `voting_open` or paused voting (`deliberation` + attempt > 0). Supports both applicant-to-applicant swaps and write-in assignments.
- **Replace form per position** — Each position row shows the current candidate and a replace form. Two radio buttons switch between an applicant dropdown (all non-withdrawn period applications) and a write-in dropdown (all active/inactive members). JS confirm prompt shows old → new name before submitting.
- **Immediate effect** — Replaces the `SlateCandidate` record in-place; votes already cast are not invalidated. A warning banner on the page makes this clear.
- **Activity logged** — Every replacement is recorded in `SlatingActivity` with `action='slate_edited'` and a `{position}: {old} → {new}` detail.
- **"Edit Slate" entry points** — Link added in the setup page action strip during `voting_open` and paused voting. Also shown as a button on the paused voting screen alongside Reopen/Reset.

---

### v2.16.6 - Slating Publish/Unpublish & Slate Change Detection (05-26-2026)
Fixes access control on the results page, adds unpublish capability, and adds a live-change notification when the slate is updated during voting.

**Deployment Status:** ✅ Deployed

**Type:** Feature / Bug Fix

**Migration required:** None

**Changes:**

- **Results page access fixed for slating manager** — `view_results` was checking `is_admin` or `is_chair` directly, excluding the `slating_manager` role. Both `can_view` and `can_publish` now use `can_manage_period()` which correctly covers admin, committee admin, committee chair, and slating manager.
- **Unpublish button** — When results are published, a small "Unpublish" button appears in the published banner on the results page. Posts to `change_period_status` with `status=voting_closed`. Only visible to users with manage-level access.
- **Slate change detection on vote page** — The vote page now polls `voting_status` API every 30 seconds. If `slate_candidate_count` changes (write-in added/removed), an amber "The slate has been updated" banner appears with a Refresh button. If the period status changes (e.g. voting paused), the page auto-reloads. Polling only runs during live voting, not on the paused screen.
- **`voting_status` API extended** — Now returns `slate_candidate_count` for the approved primary slate, enabling the client-side change detection above.

---

### v2.16.5 - Slating Pause UX & Public Slate View (05-26-2026)
Improves the pause-voting flow and adds a way for voting members to view the approved slate.

**Deployment Status:** ✅ Deployed

**Type:** Feature / UX

**Migration required:** None

**Changes:**

- **Pause voting stays on vote page** — Pausing voting now redirects the chair to the vote page instead of the setup page, where they see a yellow "Voting Paused" banner with Attempt #, a "Reopen Voting" button, and a "Clear & Reset Votes" button. The committee no longer has to navigate back to setup just to restart.
- **Reopen voting from vote page** — A `Reopen Voting` form on the paused vote page posts to `change_period_status` with `status=voting_open`, seamlessly incrementing the attempt counter.
- **`slating_vote` committee access during `deliberation`** — The view now lets committee members through when status is `deliberation` and `current_voting_attempt > 0` (paused state). Non-committee members are still blocked from voting until status returns to `voting_open`.
- **Public slate view** — New `view_approved_slate` view (`/slating/period/<id>/slate/view/`) accessible to any `voting_member_required` user during `voting_open`, `voting_closed`, or `results_published`. Shows the same `slate_preview` template with committee-only controls hidden.
- **"View Slate" button on vote page** — Non-committee members see a "View Slate" button in the page header during voting, linking to the public slate view.
- **`slate_preview.html` public mode** — Committee action buttons (Approve, Copy to Draft) are hidden when `public_view=True`; breadcrumb link to setup page is replaced with plain text; a "Back to Voting" button replaces the committee actions.

---

### v2.16.4 - Slating Auto-Created Ad Hoc Committee (05-26-2026)
Each slating period now automatically creates its own invisible ad hoc committee instead of being linked to a pre-existing one.

**Deployment Status:** ✅ Deployed

**Type:** Feature

**Migration required:** None (existing periods with an existing slating_committee link are unaffected; new periods get the auto-committee on creation)

**Changes:**

- **Auto-created ad hoc committee** — `create_period` now creates a `Committee` with `is_slating_committee=True`, `is_ad_hoc=True`, and a unique generated name (`Slating — <name> [<id>]`) immediately after creating the period, and links it as `period.slating_committee`. No committee selection is required or available.
- **Removed committee dropdown** — The "Slating Committee" select from the Basic Information form has been removed. Committee membership is now managed exclusively through the Committee Members card.
- **Period delete cleans up committee** — When a period is deleted, if its linked committee is an ad hoc committee, it is also deleted.
- **Fixed `home.py` / `officer_home.py` committee lookup** — These views were using `Committee.objects.filter(is_slating_committee=True).first()`, which would have returned an arbitrary committee with multiple ad hoc committees. Now they use `active_slating_period.slating_committee` directly.
- **Fixed `results.py` / `transfer_admin.py` committee lookup** — Same fix; these views now use `period.slating_committee` directly.

---

### v2.16.3 - Slating Committee Member & Template Fixes (05-26-2026)
Bug fixes for the committee member card and a template syntax error.

**Deployment Status:** ✅ Deployed

**Type:** Bug Fix

**Migration required:** None

**Changes:**

- **`TemplateSyntaxError` fix** — The "Clear & Reset Votes" `onclick` contained a `|yesno:` filter expression inside a JS string, which Django's template parser cannot handle. Replaced with plain text; the attempt number is interpolated normally via `{{ period.current_voting_attempt }}`.
- **Committee admin shown in member list** — `committee.admin` is a FK field separate from the `members` M2M. The view now prepends the committee admin to `committee_members` if they are not already in the M2M list, so they appear in the display correctly.
- **`_user_can_view` recognizes committee admin** — `can_view_applications()` and `slating_committee_required` now check `committee.admin_id == user.pk` in addition to `is_member()`, so the committee admin can access applications and interview notes without needing to also be in the members M2M.
- **Clearer no-committee warning** — When `period.slating_committee` is not set, the Committee Members card now shows an amber note explaining a committee must be linked via the Basic Information form first, instead of a generic grey message.
- **Dropdown duplicate filter removed** — The `{% if m not in committee_members %}` check on the Add dropdown was an unreliable Django template object comparison. Removed; the M2M silently ignores re-adds so it is safe to show all eligible members.

---

### v2.16.2 - Slating Write-in Markers, Vote Reset & Minor Fixes (05-26-2026)
Three additions to the slating voting and write-in flows.

**Deployment Status:** ✅ Deployed

**Type:** Feature / Bug Fix

**Migration required:** None

**Changes:**

- **Write-in member markers** — The member dropdown in the Blank Positions card is now disabled until a position is selected. Once a position is chosen, JS rebuilds the dropdown with optgroups and markers: applicants who listed the position as a preference are shown first with a ★ and their choice tier (1st/2nd/3rd choice); members already on the slate in any capacity show a ● marker and are grouped last. Data (applicants-by-position, already-slated IDs, full member list) is embedded as a JSON blob via `write_in_js_data` context variable built in `edit_period`.
- **Vote reset** — New `reset_votes` view (POST, chair-only) deletes all `SlatingBallot` and `SlatingVote` records for the current paused attempt and decrements `current_voting_attempt`. Accessible via a "Clear & Reset Votes" button that appears in the action banner during `deliberation` when `current_voting_attempt > 0`. Requires double confirmation. URL: `slating/period/<id>/reset-votes/`.
- **Paused-voting banner** — When voting has been paused (`deliberation` + `current_voting_attempt > 0`), the banner now shows "Resume Voting" (re-opens with the same vote type) and "Clear & Reset Votes" side by side, plus a note showing the current attempt number. The original "Open Voting" form (with vote type selector and quorum) only shows when `current_voting_attempt == 0`.
- **`import json` moved inline** — The `json` import inside `edit_period` for building `write_in_js_data` is a local inline import to avoid polluting the module namespace; all model imports remain at the top level.

---

### v2.16.1 - Slating Confidentiality & Voting Controls (05-26-2026)
Three targeted fixes to the slating module: pause voting, committee access via the admin FK, and full confidentiality enforcement.

**Deployment Status:** ✅ Deployed

**Type:** Feature / Security

**Migration required:** None

**Changes:**

- **Pause Voting button** — During `voting_open`, the setup banner now shows two buttons: "Pause Voting" (reverts to `deliberation`, decrements `current_voting_attempt` so re-opening doesn't double-count) and "Close Voting" (permanent, triggers result calculation). Pause is styled yellow; Close remains red.
- **Committee admin access** — `slating_chair_required` and `can_manage_period` now recognize `Committee.admin` FK in addition to `Committee.chairs` M2M. Previously, being set as committee admin granted no access to the period setup page.
- **Confidentiality enforcement** — Permissions rewritten with a "locked" mode. Once a period has a `slating_manager` or a committee admin assigned, site admin status alone no longer grants access. Only explicitly authorized roles (committee admin, committee chair, slating_manager, committee members for read access) are permitted. Unlocked periods (no manager/admin set) retain the site admin fallback so initial setup still works. `is_committee` checks in `vote.py` and `results.py` updated to use `can_view_applications()` helper, which respects the same locked/unlocked logic.

---

### v2.16.0 - Slating Voting Session & Attendance System (05-26-2026)
Major additions to the officer slating module: attendance tracking for voting sessions, quorum enforcement, a designated slating manager role, committee member management, and several UX improvements to the period setup page.

**Deployment Status:** ✅ Deployed

**Type:** Feature

**Migrations required:** `0167_slatingperiod_quorum_slatingattendance`, `0168_slatingperiod_slating_manager`

**Changes:**

- **Attendance system** — New `SlatingAttendance` model tracks which members are present for a slating voting session. Members not marked present are blocked from voting with a clear error message. Managed via a dedicated `/slating/period/<id>/attendance/` page (chair-only) showing all eligible members with mark present / mark absent toggles.
- **Quorum** — Optional `quorum` integer field on `SlatingPeriod`. Set when "Open Voting" is triggered from the deliberation banner. Attendance page shows a live quorum badge (met / not met, how many more needed). Sidebar attendance link shows a red "No Quorum" badge when quorum is not met.
- **Slating manager** — New `slating_manager` FK on `SlatingPeriod`. Designated manager has full setup access (equivalent to committee chair) without needing to be a site admin. Assignable by admins only via a dropdown in the Basic Information form. `slating_chair_required` decorator and `can_manage_period()` helper both updated to recognize this role.
- **Committee member management** — Slating manager/chair can add and remove committee members directly from the period setup page. Committee members retain access to view applications and interview notes. Separate card in the main content area with a dropdown to add eligible members.
- **"Go Vote" button** — Shown in the sidebar Quick Actions during `voting_open` so the slating chair can navigate to the vote page without leaving setup context.
- **"Back to Setup" button** — Shown on the vote page header for committee members, allowing quick return to the setup page while voting is open.
- **Write-in eligible members** — Write-in candidate selector now correctly limits to `member_status__in=['Active', 'Inactive']` and `member_type__in=['Member', 'Chair', 'Officer']` — excludes pledges, advisors, alumni, and removed members.
- **Runoff & write-in template fixes** — All slating templates (`vote.html`, `vote_individual.html`, `slate_builder.html`, `slate_preview.html`, `results.html`, `period_setup.html`) updated to use `candidate.candidate_name` instead of `candidate.application.applicant.name`, preventing crashes when a `SlateCandidate` is a write-in with no application.
- **Date fields now display saved values** — Added `{% load tz %}` and `|localtime|date:` to all `datetime-local` inputs in the period setup form. Also fixed `parse_datetime` to call `make_aware()` for naive datetimes on save, preventing dates from silently being stored as UTC and then appearing blank on re-load.
- **Sidebar condensed** — Stats and Quick Actions merged into a single compact card with smaller counts and no icons. Reduces vertical height significantly.
- **`period_setup.py` import cleanup** — All inline `from src.models import ...` statements inside view functions replaced with top-level imports, fixing `UnboundLocalError` caused by Python's function-scope variable hoisting.

---

### v2.15.0 - User Watch Flag + Performance Improvements (05-23-2026)
New `UserWatchFlag` model allows admins to secretly flag a user for monitoring. When active, any successful login or ≥2 repeated failed login attempts trigger an immediate alert email (HTML, with geo/IP/device/risk details) to the security alert address and create a `LoginAlert` record. Managed from the Admin-v2 Login Security page with add/edit/pause/remove controls. Also replaces the bcrypt-based `has_default_password()` method with a cached `BooleanField`, eliminating the slow popup load on the officer manage users page. Migration required (backfills existing users via one-time bcrypt check).

**Deployment Status:** ✅ Deployed

**Type:** Feature / Security

### v2.14.1 - Archive Detail Pages (05-12-2026)
7 standalone detail pages (Officer Duties, Committee Details, Kai Procedures, Slating & Elections, Advisors, Academic Standards, Passed Resolutions) archived and removed from routing. All links to these pages removed from `constitution_bylaws.html`, `roberts_rules.html`, and `manage_resolutions.html`. No migration required.

**Deployment Status:** ✅ Deployed

**Type:** Cleanup

### v2.14.0 - UserPreferences JSON Consolidation (05-11-2026)
20 boolean preference columns (`email_announcements`, `show_vote_menu`, `notify_announcements`, etc.) consolidated into a single `prefs` JSONField. Properties with identical names maintain the existing interface — templates, notification service, and most view code unchanged. New preferences now require only a default value, not a schema migration. `UserPreferencesForm` rewritten as a plain Form with `instance=` support and a `save()` method. ORM filter queries updated to use JSON path traversal. Migration `0158`.

**Deployment Status:** Deployed

**Type:** Feature / Code Quality

---

### v2.13.4 - JSON Storage Migration (05-11-2026)
Three model fields migrated from comma-separated strings to `JSONField`: `KaiReport.tags`, `KaiReportTemplate.suggested_tags`, and `SystemLockdown.whitelisted_ips`. `is_ip_whitelisted()` simplified from a manual split to `return ip in self.whitelisted_ips`. `KaiReport.get_tags_list()` simplified to return the field directly. All write paths (views, forms) and display paths (templates, CSV exports) updated. Migration `0157` handles data conversion.

**Deployment Status:** Deployed

**Type:** Code Quality / Maintainability

---

### v2.13.3 - Decorator Hardening (05-10-2026)
Four decorator layer fixes: (1) `@wraps(view_func)` added to all six decorators in `src/decorators.py` — without it, Django sees every decorated view as a function named `wrapper`. (2) `kai_chair_required` and `bug_admin_required` moved from view files into `src/decorators.py`; a weaker copy of `require_admin_v2_auth` in `notification_admin.py` replaced with an import of the real one from `admin_v2.py`. (3) Redundant `@login_required` removed from 17 views across 6 files — all custom decorators already check authentication. (4) Unused `login_required` imports removed.

**Deployment Status:** Deployed

**Type:** Code Quality / Maintainability

**Migration required:** None

**Changes:**
- **`@wraps` added** — `log_function_call`, `committee_chair_required`, `officer_required`, `officer_or_advisor_required`, `admin_required`, `exclude_pledges`
- **Decorator consolidation** — `kai_chair_required` and `bug_admin_required` moved to `src/decorators.py`; `notification_admin.py` now imports the full `require_admin_v2_auth` instead of its own weaker copy
- **`@login_required` dead code removed** — 17 views across `service_hours.py`, `activity_logs.py`, `service_form_builder.py`, `submit_new_version.py`, `upload_legislation.py`, `reopen_legislation.py`

---

### v2.13.2 - Magic Strings, Wildcard Imports & Committee Flag Consistency (05-10-2026)
Three code quality improvements: (1) `src/constants.py` added with `MemberType`, `MemberStatus`, and `CommitteeCode` — replaces bare string literals across models, views, and decorators. (2) All wildcard imports removed from `src/urls.py`, `src/decorators.py`, and five view files. (3) KAI and CHAPTER committees brought in line with EXEC/SLATING — now use `is_kai_committee` and `is_chapter_committee` boolean flags instead of hardcoded code comparisons; EXEC's one remaining code comparison in `is_chair()` also fixed.

**Deployment Status:** Deployed

**Type:** Code Quality / Maintainability

**Migration required:** `0155_committee_kai_and_chapter_flags`

**Changes:**
- **`src/constants.py`** — `MemberType`, `MemberStatus`, `CommitteeCode` constant classes
- **Magic strings eliminated** — member type/status comparisons in `src/models.py`, `src/decorators.py`, and four view files
- **Wildcard imports removed** — `src/urls.py` (3 wildcards), `src/decorators.py`, 5 view files
- **`is_kai_committee` flag** — added to `Committee` model; replaces 15+ `code='KAI'` comparisons across `kai_form_builder.py`, `kai_reports.py`, `kai_user_dashboard.py`, `global_search.py`, `committee_detail.py`
- **`is_chapter_committee` flag** — added to `Committee` model; replaces `code='CHAPTER'` in `manage_chapter_documents.py`
- **EXEC `is_chair()` fix** — `self.code == 'EXEC'` → `self.is_exec_board` (the flag already existed; this was a missed instance)

---

### v2.13.1 - CSP Nonce Hardening (05-10-2026)
Removes `'unsafe-inline'` from `script-src` by replacing it with a per-request cryptographic nonce across all templates.

**Deployment Status:** Deployed

**Type:** Security Update

**Security:**

- **CSP `script-src` — Remove `unsafe-inline`**: A random nonce is generated per request (`secrets.token_urlsafe(16)`) and attached to `request.csp_nonce`. `InputSanitizationMiddleware` now emits `script-src 'self' 'nonce-{nonce}'` instead of `'unsafe-inline'`. All inline `<script>` tags across every template now carry `nonce="{{ request.csp_nonce }}"`. Any injected script without the nonce is blocked by the browser before it executes, even if it reaches the page

---

### v2.13.0 - Security Patch (05-06-2026)
Critical security fixes for IP spoofing, stored XSS, and honeypot ban propagation.

**Deployment Status:** Deployed

**Type:** Security Update

**Security:**

- **IP Spoofing Fix**: All `get_client_ip()` functions now read the rightmost `X-Forwarded-For` entry (appended by nginx) instead of the leftmost (attacker-controlled). Previously, attackers could send arbitrary `X-Forwarded-For` headers to appear as a different IP on every request, bypassing login rate limiting, IP blacklisting, geo-restriction, and honeypot bans entirely
- **Honeypot Ban Propagation**: Honeypot-triggered bans now write to the `IPBlacklist` database table so the block enforced on all endpoints immediately and survives cache flushes/server restarts. Previously, the ban was cache-only and only prevented repeat honeypot hits — the attacker could still reach login and all other pages freely
- **Stored XSS — Landing Page**: Officer-authored landing page HTML (via Quill editor) is now sanitized with `bleach` before saving to the database. Prevents officers from injecting `<script>` tags or event handlers that would execute for all public visitors
- **Stored XSS — Vote Results**: Replaced `{{ vote_breakdown.keys|safe }}` with Django's `json_script` tag and `JSON.parse()`. Officers can no longer inject JavaScript via plurality vote option names
- **Stored XSS — Guide Articles**: Guide article content is now sanitized with `bleach` in the view before rendering, preventing a compromised admin account from injecting JavaScript for all authenticated users

**Dependencies:**

- Added `bleach==6.2.0` for HTML sanitization

---

### v2.12.0 - Lockout Management, Bug Fixes & UI Improvements (04-15-2026)
Security hardening, bug fixes, and UI overhaul for the legislation history page.

**Deployment Status:** Deployed

**Type:** Security, Bug Fix & UI

**Security:**

- **Login Lockout Management Page**: New `/admin-v2/security/lockouts/` page showing all active lockouts with per-row Clear, Blacklist IP, and Whitelist+Clear actions, plus a bulk "Clear All Active" button
- **Whitelist Bypass Fix**: Whitelisted IPs could still be locked out due to both rate-limiting systems (`login_view.py` and `LoginRateLimitMiddleware`) ignoring the `IPWhitelist` table — both now check whitelist before applying any lockout
- **LoginLockout Model**: New DB model persists lockout events from all three rate-limiting sources (login_view IP, middleware IP, middleware username) so admins can see and manage them from the dashboard
- **Security Dashboard**: Added Active Lockouts stat card and Login Lockouts quick-action button to security dashboard

**Bug Fixes:**

- **Deliberation Email Crash**: Fixed `UnboundLocalError: cannot access local variable 'message'` in `src/view/kai_reports.py` — `message` was uninitialized when `deliberation_outcome` didn't match any branch
- **Initiate Pledge Tracebacks Invisible**: Errors in `initiate_pledges` were silently swallowed because the logger (`src.view.officer.manage_members`) had no file handler — added `logging.getLogger('admin_actions')` call in the except block so full tracebacks now appear in the production log
- **Missing FK in Initiate**: Added `('src_loginlockout', 'cleared_by_id')` to the `related_tables` list in `initiate_pledges` to prevent FK constraint errors after `LoginLockout` model was added

**UI:**

- **Legislation History Page — Complete Overhaul**: Personal dashboard layout replacing the old generic card grid:
  - Personal stats row (Total / Passed / Failed / Active) with colored left-border stat boxes
  - Pill-style filter buttons (All / Active+Pending / Passed / Failed / Tabled) with live counts
  - Compact list rows with colored left-border status strip instead of full gradient headers
  - Inline vote summaries per vote mode (plurality: option pills + winner; piecewise: one-liner with required count; percentage: inline progress bar with pass/fail coloring)
  - Author vs Co-author role badge per row
  - Action row split into text links (View Details · Document · Download) and action buttons (Reopen / Edit / Submit New Version)
  - Now includes co-authored legislation, not just submitted
  - Mobile-responsive throughout
- **Legislation Detail Page — Mobile Fixes**: Percentage vote grid (`grid-cols-3`) now uses `gap-2 sm:gap-4`, `p-3 sm:p-4`, and `text-2xl sm:text-3xl` to prevent squeeze on narrow screens; individual vote names get `min-w-0 truncate` so long names don't push badges off-screen
- **Legislation Tracker Page — Mobile Fixes**: Action button rows use `grid grid-cols-1 sm:flex sm:flex-wrap` with reduced mobile padding; status tabs scroll horizontally with `overflow-x-auto whitespace-nowrap`

**Files Modified:**

- `src/models.py` — Added `LoginLockout` model
- `src/view/login_view.py` — Added `IPWhitelist` check, `LoginLockout` persistence on lockout
- `src/middleware/security.py` — Added `_is_ip_whitelisted()` with cache, `LoginLockout` persistence
- `src/view/admin_v2.py` — Added `manage_lockouts` view, updated `security_dashboard` context
- `src/view/kai_reports.py` — Fixed deliberation email `UnboundLocalError`
- `src/view/officer/manage_members.py` — Added `admin_actions` logger in initiate except block, added `src_loginlockout` FK to `related_tables`
- `src/view/view_legislation_history.py` — Full rewrite: status filters, vote-mode-aware data, co-author support
- `src/urls.py` — Added `manage_lockouts` URL
- `templates/admin_v2/lockouts.html` — **NEW** lockout management page
- `templates/admin_v2/security_dashboard.html` — Added lockout stat card and quick-action button
- `templates/legislation_history.html` — Complete redesign
- `templates/passed_legislation.html` — Mobile responsive fixes
- `templates/src/legislation_detail.html` — Mobile responsive fixes

---

### v2.11.0 - Security Attack Mitigation & Admin Dashboard Redesign (04-07-2026)
Major security update with attack mitigation tools, honeypot traps, emergency lockdown system, and a complete admin dashboard redesign with modern card-based UI.

**Deployment Status:** Deployed

**Type:** Security Enhancement & UI Redesign

**Security Features:**

- **Session Tracking Middleware**: Fixed active sessions not displaying on preferences page by tracking sessions on each authenticated request (throttled to 5-minute intervals)
- **Auto-Quarantine System**: Automatically quarantines accounts showing attack patterns (20+ attacks from IP triggers quarantine)
- **Honeypot/Poison Pill Endpoints**: Fake admin URLs that trap attackers:
  - `/wp-admin/`, `/phpmyadmin/`, `/.env`, `/admin/backup/`, `/api/v1/users/export/`
  - Any access triggers immediate 24-hour IP ban and security alert
- **Emergency Lockdown Mode**: One-click system lockdown blocking all logins except whitelisted IPs
- **Security Email Notifications**: Critical security alerts sent to configured email address:
  - Attack blocks (10+ attacks from same IP)
  - Multiple failed logins (5+ from same IP in 15 minutes)
  - Account quarantines
  - Honeypot triggers
  - Lockdown activation/deactivation
- **Security Notification Logs**: Full audit trail of all security alerts with severity levels

**Admin Dashboard Redesign:**

- **Card-Based Layout**: Modern expandable/collapsible cards using Alpine.js
- **Security Card**: Prominent security overview with attack alerts, quarantined accounts, blocked IPs
- **Quick Stats Grid**: At-a-glance statistics for users, logins, votes, events
- **Organized Sections**: Security, Users, Content, Email, System, Performance cards
- **Lockdown Alert Banner**: Visible warning when system is in lockdown mode
- **Dark Mode Support**: Full dark mode support throughout redesigned dashboard
- **Responsive Design**: Works on desktop, tablet, and mobile

**New Admin-v2 Pages:**

- `/admin-v2/security/` - Security dashboard with attack statistics
- `/admin-v2/security/quarantine/` - Quarantine management (view, release accounts)
- `/admin-v2/security/lockdown/` - Emergency lockdown control panel
- `/admin-v2/security/honeypot/` - Honeypot access logs
- `/admin-v2/security/notifications/` - Security notification history

**Database Changes:**

- Added `is_quarantined` field to `ParliamentUser` model
- Added `QuarantinedAccount` model for tracking quarantined accounts with release workflow
- Added `HoneypotAccess` model for logging honeypot trap access attempts
- Added `SystemLockdown` model for managing emergency lockdown state
- Added `SecurityNotificationLog` model for security alert audit trail

**New Files:**

- `src/middleware/session_tracking.py` - Session tracking middleware
- `src/middleware/lockdown.py` - Emergency lockdown enforcement middleware
- `src/security_notifications.py` - Security alert email functions
- `src/view/honeypot.py` - Honeypot trap view handlers
- `templates/lockdown.html` - User-facing lockdown page
- `templates/admin_v2/security_dashboard.html` - Security dashboard
- `templates/admin_v2/quarantine_management.html` - Quarantine management UI
- `templates/admin_v2/lockdown_control.html` - Lockdown control panel
- `templates/admin_v2/honeypot_logs.html` - Honeypot access logs
- `templates/admin_v2/security_notifications.html` - Security notification history

**Files Modified:**

- `src/models.py` - Added 4 new security models and is_quarantined field
- `src/middleware/security.py` - Added auto-quarantine triggers and notification calls
- `src/view/admin_v2.py` - Added 5 new security management views, dashboard context updates
- `src/view/login_view.py` - Added quarantine check before login
- `src/urls.py` - Added honeypot and security management URL patterns
- `Parliament/settings_postgres.py` - Added new middleware and SECURITY_ALERT_EMAIL setting
- `templates/admin_v2/dashboard.html` - Complete redesign with card-based layout

**Environment Variables:**

- `SECURITY_ALERT_EMAIL` - Email address for critical security alerts (required)

---

### v2.10.0 - Songbook Lyrics & Pledge Initiation Fixes (04-03-2026)
Songbook feature enhancements with complete lyrics for all songs, plus critical fixes for pledge initiation.

**Deployment Status:** Deployed

**Type:** Feature Enhancement & Bug Fix

**Songbook Enhancements:**

- **Complete Song Lyrics**: Clean, properly formatted lyrics for 40 songs extracted from "Beta Theta Pi Song Book Revised 2005" (Beta Tunes)
- **Lyrics Update Command**: Management command `update_song_lyrics` with options:
  - `--dry-run` - Preview changes without saving
  - `--force` - Overwrite existing lyrics
- **Title Alias Support**: Handles database titles that don't exactly match songbook (e.g., "As Beta Now We Meet" → "As Betas Now We Meet")
- **Songs Updated**: The Alumni's Return, As Betas Now We Meet, The Banquet Hall, Beta Day, Beta Doxology, Beta Hymn, Beta Lullaby, The Beta Marseillaise, Beta Praise, Beta Rose, The Beta Shrine, The Beta Stars, Beta Sweetheart, Beta's Emblems, The Crow Song, For The Staunchest, Gemma Nostra, I Took My Girl Out Walking, The Jolly Greeks, The Loving Cup, Marching Along, My Beta Girl, Parting Song, She Wears My Beta Pin, The Sons of the Dragon, There's a Scene, Ti-de-i-de-o, To the Pledge, We Gather Again, Wooglin Forever!, Wooglin to the Pledge, and more
- **4 Songs Unavailable**: Good Betas Sing Forever, Ring the Bells of Old Miami, We'll Always Hang Together, I Love You (Only You) Beta Girl - not included in 2005 songbook edition
- **Chorister Role**: New role that grants song management permissions
  - Members with the Chorister role can add, edit, and delete songs
  - Admins can assign/remove the role from the Manage Categories page
  - Role is automatically created when first accessing the categories page
- **Changelog Version Sorting**: Fixed sorting so v2.10.0 appears after v2.9.0 (was appearing next to v2.1.0 due to alphabetical sorting)

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
- `src/view/songbook.py` - Added Chorister role permission check and management
- `src/view/changelog.py` - Fixed semantic version sorting
- `templates/songbook_categories.html` - Added Chorister management UI
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
<li><strong>v2.11.0 - Security Attack Mitigation & Admin Dashboard Redesign</strong> (April 7, 2026)
    <ul>
    <li>Session tracking middleware (fixes active sessions display)</li>
    <li>Auto-quarantine system for accounts showing attack patterns</li>
    <li>Honeypot/poison pill endpoints to trap attackers (wp-admin, phpmyadmin, .env, etc.)</li>
    <li>Emergency lockdown mode with IP whitelisting</li>
    <li>Security email notifications for critical events</li>
    <li>New admin-v2 security dashboard with attack statistics</li>
    <li>Quarantine management, lockdown control, honeypot logs pages</li>
    <li>Complete admin dashboard redesign with card-based layout using Alpine.js</li>
    <li>4 new security models: QuarantinedAccount, HoneypotAccess, SystemLockdown, SecurityNotificationLog</li>
    </ul>
</li>
<li><strong>v2.10.0 - Songbook Lyrics & Pledge Initiation Fixes</strong> (April 3, 2026)
    <ul>
    <li>Complete song lyrics for 40 songs from 2005 Beta songbook</li>
    <li>Chorister role for song management permissions</li>
    <li>Fixed pledge initiation data loss (CASCADE delete bug)</li>
    <li>Fixed FK constraint errors during pledge initiation</li>
    <li>Changelog version sorting fix</li>
    </ul>
</li>
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
<li><strong>v2.11.0</strong> (2026-04-07) - Security Attack Mitigation & Admin Dashboard Redesign
    <ul>
    <li>Session tracking middleware fixes active sessions display</li>
    <li>Auto-quarantine system for attack detection</li>
    <li>Honeypot/poison pill endpoints trap attackers</li>
    <li>Emergency lockdown mode with IP whitelisting</li>
    <li>Security email notifications for critical events</li>
    <li>Admin-v2 security dashboard with quarantine, lockdown, honeypot management</li>
    <li>Complete admin dashboard redesign with Alpine.js card-based UI</li>
    <li>4 new security models, 2 new middleware, 5 new admin views</li>
    </ul>
</li>
<li><strong>v2.10.0</strong> (2026-04-03) - Songbook Lyrics & Pledge Initiation Fixes
    <ul>
    <li>Complete song lyrics for 40 songs from 2005 Beta songbook</li>
    <li>Chorister role for song management</li>
    <li>Fixed pledge initiation data loss and FK constraint errors</li>
    <li>Fixed changelog version sorting</li>
    </ul>
</li>
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

**Last Updated:** 2026-04-07
**Next Review:** 2026-05-07
