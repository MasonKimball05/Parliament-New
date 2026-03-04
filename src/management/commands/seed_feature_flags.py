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
                'description': 'Enable iCal calendar subscription functionality',
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
                'description': 'Enable channel-based chat system. When disabled, all chat functionality and polling is stopped.',
                'category': 'features',
                'is_enabled': True,
            },
            {
                'name': 'committee_chat',
                'display_name': 'Committee Chat',
                'description': 'Enable committee-specific chat channels',
                'category': 'features',
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
                self.stdout.write(f'  - Feature flag already exists: {flag.display_name}')

        # Page Toggles
        page_toggles = [
            {
                'page_name': 'home',
                'display_name': 'Home Page',
                'description': 'Main dashboard/home page',
                'is_enabled': True,
            },
            {
                'page_name': 'legislation',
                'display_name': 'Legislation',
                'description': 'Legislation listing and voting pages',
                'is_enabled': True,
            },
            {
                'page_name': 'passed_legislation',
                'display_name': 'Passed Legislation',
                'description': 'Archive of passed legislation',
                'is_enabled': True,
            },
            {
                'page_name': 'calendar',
                'display_name': 'Calendar',
                'description': 'Events calendar page',
                'is_enabled': True,
            },
            {
                'page_name': 'announcements',
                'display_name': 'Announcements',
                'description': 'Announcements page',
                'is_enabled': True,
            },
            {
                'page_name': 'chapter_documents',
                'display_name': 'Chapter Documents',
                'description': 'Chapter documents and files',
                'is_enabled': True,
            },
            {
                'page_name': 'committees',
                'display_name': 'Committees',
                'description': 'Committee pages and management',
                'is_enabled': True,
            },
            {
                'page_name': 'committee_home',
                'display_name': 'Committee Home',
                'description': 'Individual committee home pages',
                'is_enabled': True,
            },
            {
                'page_name': 'committee_documents',
                'display_name': 'Committee Documents',
                'description': 'Committee document management',
                'is_enabled': True,
            },
            {
                'page_name': 'committee_chat',
                'display_name': 'Committee Chat',
                'description': 'Committee chat channels',
                'is_enabled': True,
            },
            {
                'page_name': 'officer_home',
                'display_name': 'Officer Home',
                'description': 'Officer dashboard and tools',
                'is_enabled': True,
            },
            {
                'page_name': 'user_list',
                'display_name': 'User Directory',
                'description': 'Member directory/user list',
                'is_enabled': True,
            },
            {
                'page_name': 'profile',
                'display_name': 'User Profile',
                'description': 'User profile pages',
                'is_enabled': True,
            },
            {
                'page_name': 'kai_reports',
                'display_name': 'KAI Reports',
                'description': 'KAI report submission and viewing',
                'is_enabled': True,
            },
            {
                'page_name': 'chat_index',
                'display_name': 'Chat Channels',
                'description': 'Chat channel index and messaging',
                'is_enabled': True,
            },
            {
                'page_name': 'roberts_rules',
                'display_name': "Robert's Rules",
                'description': "Robert's Rules reference page",
                'is_enabled': True,
            },
            {
                'page_name': 'constitution_bylaws',
                'display_name': 'Constitution & Bylaws',
                'description': 'Constitution and bylaws page',
                'is_enabled': True,
            },
        ]

        for toggle_data in page_toggles:
            toggle, created = PageToggle.objects.get_or_create(
                page_name=toggle_data['page_name'],
                defaults={
                    'display_name': toggle_data['display_name'],
                    'description': toggle_data['description'],
                    'is_enabled': toggle_data['is_enabled'],
                }
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f'  ✓ Created page toggle: {toggle.display_name}'))
            else:
                self.stdout.write(f'  - Page toggle already exists: {toggle.display_name}')

        self.stdout.write(self.style.SUCCESS('\n✅ Successfully seeded feature flags and page toggles!'))
        self.stdout.write(self.style.SUCCESS(f'Total feature flags: {FeatureFlag.objects.count()}'))
        self.stdout.write(self.style.SUCCESS(f'Total page toggles: {PageToggle.objects.count()}'))
