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
                'description': 'Enable dark mode theme support',
                'category': 'ui',
                'is_enabled': True,
            },
            {
                'name': 'email_notifications',
                'display_name': 'Email Notifications',
                'description': 'Enable email notification system',
                'category': 'notifications',
                'is_enabled': False,
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
