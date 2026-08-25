"""
Management command to seed feature flags and page toggles
Run with: python manage.py seed_feature_flags
"""
from django.core.management.base import BaseCommand
from src.models_feature_flags import FeatureFlag, PageToggle


class Command(BaseCommand):
    help = 'Seeds feature flags and page toggles into the database'

    def handle(self, *args, **kwargs):
        self.stdout.write(self.style.SUCCESS('Starting to seed feature flags and page toggles...'))

        # Feature Flags
        feature_flags = [
            {
                'name': 'calendar_subscriptions',
                'display_name': 'Calendar Subscriptions',
                'description': (
                    'Enable the iCal calendar subscription ENDPOINTS '
                    '(/calendar/subscribe/ and token regeneration). Pairs with '
                    '"iCal Export" (ical_export), which controls the Subscribe '
                    'BUTTON on the calendar page — turn both on/off together.'
                ),
                'category': 'features',
                'is_enabled': True,
            },
            {
                # v3.16.2: this flag gates the Subscribe button in
                # templates/calendar.html ({% if feature_flags.ical_export %}).
                # It was only ever defined in seed_admin_v2.py, so installs
                # seeded via THIS command never got a row — and template flag
                # lookups fail CLOSED (missing row → falsy), so the button was
                # silently invisible even though the whole feature (model,
                # view, routes, tests) worked. Found 07-25-26.
                'name': 'ical_export',
                'display_name': 'iCal Export',
                'description': (
                    'Show the Subscribe button + modal on the calendar page. '
                    'The underlying feed endpoints are gated separately by '
                    '"Calendar Subscriptions" (calendar_subscriptions).'
                ),
                'category': 'features',
                'is_enabled': True,
            },
            {
                'name': 'global_search',
                'display_name': 'Global Search',
                'description': 'Enable site-wide search functionality',
                'category': 'features',
                'is_enabled': True,
            },
            {
                'name': 'dark_mode',
                'display_name': 'Dark Mode',
                'description': (
                    'v3.26.0: now gates real behaviour. On: the theme picker in '
                    'Preferences and the dark/light/auto toggle work as before. '
                    'Off: the theme selector is hidden, every session renders '
                    'light regardless of a member\'s saved preference, and the '
                    'inline theme-detection script in base.html is skipped.'
                ),
                'category': 'ui',
                'is_enabled': True,
            },
            {
                # ⚠️ v3.26.0 — WAS SEEDED `is_enabled: False` HERE WHILE GATING
                # NOTHING, SO IT DID NOTHING. Now that FeatureFlagGatedEmailBackend
                # reads this flag on every send, `False` would silently stop all
                # non-critical chapter email (announcements, digests, etc — the
                # backend's always-send allowlist is security/auth mail only).
                # Flipped to True here to preserve current behaviour (email has
                # always gone out). ⚠️ If a row for this flag ALREADY EXISTS on
                # prod, this seed command will NOT change its is_enabled value —
                # get_or_create only sets defaults on first creation, and the
                # loop below deliberately never touches is_enabled on an existing
                # row (see the comment at that loop). CHECK /admin/ → Feature
                # Flags → "Email Notifications" is enabled BEFORE relying on
                # this deploy shipping email unchanged.
                'name': 'email_notifications',
                'display_name': 'Email Notifications',
                'description': (
                    'v3.26.0: now gates real behaviour via '
                    'FeatureFlagGatedEmailBackend (settings.EMAIL_BACKEND). Off: '
                    'ordinary chapter email (announcements, digests, welcome '
                    'mail, etc.) is silently dropped. Security-critical mail '
                    '(2FA codes, password resets, email-change confirmation, '
                    'security alerts, watch-flag notices, preflight failures) '
                    'always sends regardless of this flag — see '
                    'src/email_backend.py\'s always-send allowlist.'
                ),
                'category': 'notifications',
                'is_enabled': True,
            },
            {
                'name': 'chats',
                'display_name': 'Chat System',
                'description': 'Enable channel-based chat system, including committee chats. When disabled, all chat functionality is stopped.',
                'category': 'features',
                'is_enabled': True,
            },
            {
                'name': 'announcements',
                'display_name': 'Announcements',
                'description': 'Enable the announcements system. When disabled, the announcements page returns a feature-disabled error.',
                'category': 'communications',
                'is_enabled': True,
            },
            {
                'name': 'kai_reports',
                'display_name': 'KAI Reports',
                'description': 'Enable KAI reporting system',
                'category': 'features',
                'is_enabled': True,
            },
            {
                'name': 'attendance_tracking',
                'display_name': 'Attendance Tracking',
                'description': 'Enable event-based attendance tracking',
                'category': 'features',
                'is_enabled': True,
            },
            {
                'name': 'maintenance_mode',
                'display_name': 'Maintenance Mode',
                'description': 'Put site in maintenance mode',
                'category': 'system',
                'is_enabled': False,
            },
            {
                'name': 'rest_api',
                'display_name': 'REST API',
                'description': 'Enable the /api/v1/ REST API endpoints. Disabled by default until an active use case exists.',
                'category': 'features',
                'is_enabled': False,
            },
            {
                'name': 'api_token_auto_approve',
                'display_name': 'API Token Auto-Approve',
                'description': 'When enabled, new API token requests are automatically approved and activated. When disabled, tokens require manual admin approval before use.',
                'category': 'admin',
                'is_enabled': False,  # Require manual approval by default (safer)
            },

            # ── Constitution & Bylaws, one flag per document (v3.19.1) ───────
            #
            # These gate `GoverningDocument.enabled()`, which is what the
            # member-facing viewer and the resolution reference pickers query.
            # Officer management at /officers/cnb/* is deliberately NOT gated —
            # a document has to be editable before it is turned on.
            #
            # ⚠️ These four gate REAL BEHAVIOUR. CLAUDE.md records 16 seeded
            # flags that gate nothing and are a handoff hazard precisely because
            # they look like they do something; do not let these become the
            # 17th through 20th. If a future change stops consulting
            # `GoverningDocument.enabled()`, delete these rows in the same
            # commit.
            {
                'name': 'cnb_foreword',
                'display_name': 'C&B — Foreword',
                'description': (
                    'Show the Foreword on /constitution-bylaws/. '
                    'OFF until the new Constitution and Bylaws pass chapter vote — the '
                    'seeded text is the real foreword, staged ahead of the vote. '
                    'This flag is also listed in FeatureFlag.DISABLED_BY_DEFAULT, '
                    'so a missing row reads as DISABLED rather than enabled; that is '
                    'deliberate and is what stops an un-seeded install publishing '
                    'unpassed governance.'
                ),
                'category': 'documents',
                'is_enabled': False,
            },
            {
                'name': 'cnb_constitution',
                'display_name': 'C&B — Constitution',
                'description': (
                    'Show the Constitution on /constitution-bylaws/ and in the '
                    'resolution reference picker. On by default — this is governance '
                    'in force. Turn off only to stage a replacement.'
                ),
                'category': 'documents',
                'is_enabled': True,
            },
            {
                'name': 'cnb_bylaws',
                'display_name': 'C&B — Bylaws',
                'description': (
                    'Show the Bylaws on /constitution-bylaws/ and in the resolution '
                    'reference picker. On by default — this is governance in force.'
                ),
                'category': 'documents',
                'is_enabled': True,
            },
            {
                'name': 'cnb_appendix',
                'display_name': 'C&B — Appendix',
                'description': (
                    'Show the Appendix on /constitution-bylaws/ and in the resolution '
                    'reference picker. On by default — this is governance in force.'
                ),
                'category': 'documents',
                'is_enabled': True,
            },

            # ── The other 8 of the "10 dead feature flags" (v3.26.0) ─────────
            #
            # All 10 were seeded rows that gated nothing in code — a handoff
            # hazard because several have security-sounding names. Every one is
            # now wired to real behaviour; every one seeds `is_enabled: True` so
            # a deploy of this release changes nothing observable by default —
            # each flag is an off switch for something that already works today,
            # not an on switch for something new.
            {
                'name': 'legislation_voting',
                'display_name': 'Legislation Voting',
                'description': (
                    'v3.26.0: gates chapter legislation upload/edit/delete, '
                    'opening/reopening voting, runoffs, and pushing a committee '
                    'vote to chapter. Off: those endpoints 403. Does not affect '
                    'committee-internal voting — see "Committee Voting".'
                ),
                'category': 'features',
                'is_enabled': True,
            },
            {
                'name': 'anonymous_voting',
                'display_name': 'Anonymous Voting',
                'description': (
                    'v3.26.0: gates whether legislation (chapter or committee) '
                    'may be marked anonymous when created. Off: new legislation '
                    'is always attributed, regardless of what the uploader '
                    'requests. Existing anonymous votes are unaffected.'
                ),
                'category': 'features',
                'is_enabled': True,
            },
            {
                'name': 'abstain_voting',
                'display_name': 'Abstain Voting',
                'description': (
                    'v3.26.0: gates whether legislation (chapter or committee) '
                    'may allow an Abstain option. Off: new legislation never '
                    'offers Abstain, regardless of what the uploader requests.'
                ),
                'category': 'features',
                'is_enabled': True,
            },
            {
                'name': 'committee_system',
                'display_name': 'Committee System',
                'description': (
                    'v3.26.0: gates the committee index, committee home pages, '
                    'creating/managing/deleting committees, and the Committees '
                    'nav item. Off: those routes 403 and the nav item is hidden '
                    '(still subject to a member\'s own show_committees_menu '
                    'preference). Named limitation: deep-linked committee '
                    'sub-pages (chat, documents, attendance, minutes, '
                    'recruitment, education) are not individually gated by this '
                    'flag — only the index/home/management surface is.'
                ),
                'category': 'features',
                'is_enabled': True,
            },
            {
                'name': 'committee_voting',
                'display_name': 'Committee Voting',
                'description': (
                    'v3.26.0: gates committee-internal voting — casting a '
                    'committee vote, viewing committee vote results, runoffs, '
                    'recalculation, deleting a committee vote, and pushing a '
                    'committee vote to chapter. Off: those routes 403. Does not '
                    'affect chapter-level voting — see "Legislation Voting".'
                ),
                'category': 'features',
                'is_enabled': True,
            },
            {
                'name': 'event_attendance',
                'display_name': 'Event Attendance',
                'description': (
                    'v3.26.0: a narrower, additive gate on top of '
                    '"Attendance Tracking" — specifically the attendance-taking '
                    'surfaces (event attendance list, marking attendance, the '
                    'attendance dashboard, member attendance detail, the '
                    'attendance page, and /my-attendance/). Both flags must be '
                    'on for these to work; this one lets attendance-TAKING be '
                    'turned off independently of excuses — see "Excuse System".'
                ),
                'category': 'features',
                'is_enabled': True,
            },
            {
                'name': 'excuse_system',
                'display_name': 'Excuse System',
                'description': (
                    'v3.26.0: a narrower, additive gate on top of '
                    '"Attendance Tracking" — specifically excuse submission and '
                    'review (my excuses, submitting/cancelling an excuse, '
                    'reviewing excuses, serving excuse documents). Both flags '
                    'must be on for these to work; this one lets excuse-taking '
                    'be turned off independently of attendance — see '
                    '"Event Attendance".'
                ),
                'category': 'features',
                'is_enabled': True,
            },
            {
                'name': 'document_versioning',
                'display_name': 'Document Versioning',
                'description': (
                    'v3.26.0: the `DocumentVersion` model existed with zero '
                    'writers anywhere in the codebase before this release — '
                    'there was no "replace this file" action to gate, only '
                    'delete-and-re-upload-as-new. This flag gates the feature\'s '
                    'own existence: replacing a committee document\'s file '
                    '(archiving the old one as a version) and downloading a past '
                    'version. Off: chairs can still delete and re-upload '
                    'documents as before; they just cannot replace-with-history.'
                ),
                'category': 'features',
                'is_enabled': True,
            },
        ]

        for flag_data in feature_flags:
            flag, created = FeatureFlag.objects.get_or_create(
                name=flag_data['name'],
                defaults={
                    'display_name': flag_data['display_name'],
                    'description': flag_data['description'],
                    'category': flag_data['category'],
                    'is_enabled': flag_data['is_enabled'],
                }
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f'  ✓ Created feature flag: {flag.display_name}'))
            else:
                # Always sync display_name, description, and category — but never touch is_enabled
                # (it's live state that admins may have changed).
                updated_fields = []
                if flag.display_name != flag_data['display_name']:
                    flag.display_name = flag_data['display_name']
                    updated_fields.append('display_name')
                if flag.description != flag_data['description']:
                    flag.description = flag_data['description']
                    updated_fields.append('description')
                if flag.category != flag_data['category']:
                    flag.category = flag_data['category']
                    updated_fields.append('category')
                if updated_fields:
                    flag.save(update_fields=updated_fields)
                    self.stdout.write(self.style.WARNING(f'  ↺ Updated {flag.display_name}: {", ".join(updated_fields)}'))
                else:
                    self.stdout.write(f'  - Feature flag already up to date: {flag.display_name}')

        # Page Toggles
        page_toggles = [
            {
                'url_name': 'home',
                'display_name': 'Home Page',
                'description': 'Main dashboard/home page',
                'is_enabled': True,
            },
            {
                'url_name': 'legislation',
                'display_name': 'Legislation',
                'description': 'Legislation listing and voting pages',
                'is_enabled': True,
            },
            {
                'url_name': 'passed_legislation',
                'display_name': 'Passed Legislation',
                'description': 'Archive of passed legislation',
                'is_enabled': True,
            },
            {
                'url_name': 'calendar',
                'display_name': 'Calendar',
                'description': 'Events calendar page',
                'is_enabled': True,
            },
            {
                'url_name': 'announcements',
                'display_name': 'Announcements',
                'description': 'Announcements page',
                'is_enabled': True,
            },
            {
                'url_name': 'chapter_documents',
                'display_name': 'Chapter Documents',
                'description': 'Chapter documents and files',
                'is_enabled': True,
            },
            {
                'url_name': 'committees',
                'display_name': 'Committees',
                'description': 'Committee pages and management',
                'is_enabled': True,
            },
            {
                'url_name': 'committee_home',
                'display_name': 'Committee Home',
                'description': 'Individual committee home pages',
                'is_enabled': True,
            },
            {
                'url_name': 'committee_documents',
                'display_name': 'Committee Documents',
                'description': 'Committee document management',
                'is_enabled': True,
            },
            {
                'url_name': 'committee_chat',
                'display_name': 'Committee Chat',
                'description': 'Committee chat channels',
                'is_enabled': True,
            },
            {
                'url_name': 'officer_home',
                'display_name': 'Officer Home',
                'description': 'Officer dashboard and tools',
                'is_enabled': True,
            },
            {
                'url_name': 'user_list',
                'display_name': 'User Directory',
                'description': 'Member directory/user list',
                'is_enabled': True,
            },
            {
                'url_name': 'profile',
                'display_name': 'User Profile',
                'description': 'User profile pages',
                'is_enabled': True,
            },
            {
                'url_name': 'kai_reports',
                'display_name': 'KAI Reports',
                'description': 'KAI report submission and viewing',
                'is_enabled': True,
            },
            {
                'url_name': 'chat_index',
                'display_name': 'Chat Channels',
                'description': 'Chat channel index and messaging',
                'is_enabled': True,
            },
            {
                'url_name': 'roberts_rules',
                'display_name': "Robert's Rules",
                'description': "Robert's Rules reference page",
                'is_enabled': True,
            },
            {
                'url_name': 'constitution_bylaws',
                'display_name': 'Constitution & Bylaws',
                'description': 'Constitution and bylaws page',
                'is_enabled': True,
            },
        ]

        for toggle_data in page_toggles:
            toggle, created = PageToggle.objects.get_or_create(
                url_name=toggle_data['url_name'],
                defaults={
                    'display_name': toggle_data['display_name'],
                    'description': toggle_data['description'],
                    'is_enabled': toggle_data['is_enabled'],
                }
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f'  ✓ Created page toggle: {toggle.display_name}'))
            else:
                updated_fields = []
                if toggle.display_name != toggle_data['display_name']:
                    toggle.display_name = toggle_data['display_name']
                    updated_fields.append('display_name')
                if toggle.description != toggle_data['description']:
                    toggle.description = toggle_data['description']
                    updated_fields.append('description')
                if updated_fields:
                    toggle.save(update_fields=updated_fields)
                    self.stdout.write(self.style.WARNING(f'  ↺ Updated {toggle.display_name}: {", ".join(updated_fields)}'))
                else:
                    self.stdout.write(f'  - Page toggle already up to date: {toggle.display_name}')

        self.stdout.write(self.style.SUCCESS('\n✅ Successfully seeded feature flags and page toggles!'))
        self.stdout.write(self.style.SUCCESS(f'Total feature flags: {FeatureFlag.objects.count()}'))
        self.stdout.write(self.style.SUCCESS(f'Total page toggles: {PageToggle.objects.count()}'))
