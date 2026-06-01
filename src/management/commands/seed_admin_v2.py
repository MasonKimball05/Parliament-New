from django.core.management.base import BaseCommand
from src.models_feature_flags import FeatureFlag, PageToggle


class Command(BaseCommand):
    help = 'Seeds initial feature flags and page toggles for Admin v2'

    def handle(self, *args, **options):
        self.stdout.write('Seeding Admin v2 feature flags and page toggles...')

        # Feature Flags
        feature_flags = [
            # Core Features
            {
                'name': 'dark_mode',
                'display_name': 'Dark Mode',
                'description': 'Enable/disable dark mode theme across the site',
                'category': 'core',
                'is_enabled': True
            },
            {
                'name': 'announcements',
                'display_name': 'Announcements System',
                'description': 'Enable/disable the announcements feature',
                'category': 'communications',
                'is_enabled': True
            },
            {
                'name': 'calendar',
                'display_name': 'Calendar',
                'description': 'Enable/disable the calendar feature',
                'category': 'events',
                'is_enabled': True
            },
            {
                'name': 'global_search',
                'display_name': 'Global Search',
                'description': 'Enable/disable global search functionality',
                'category': 'core',
                'is_enabled': True
            },

            # Voting & Legislation
            {
                'name': 'legislation_voting',
                'display_name': 'Legislation Voting',
                'description': 'Enable/disable the ability to create and vote on legislation',
                'category': 'voting',
                'is_enabled': True
            },
            {
                'name': 'anonymous_voting',
                'display_name': 'Anonymous Voting',
                'description': 'Allow users to vote anonymously',
                'category': 'voting',
                'is_enabled': True
            },
            {
                'name': 'abstain_voting',
                'display_name': 'Abstain Option in Voting',
                'description': 'Allow users to abstain from votes',
                'category': 'voting',
                'is_enabled': True
            },

            # Committees
            {
                'name': 'committee_system',
                'display_name': 'Committee System',
                'description': 'Enable/disable the entire committee system',
                'category': 'committees',
                'is_enabled': True
            },
            {
                'name': 'committee_voting',
                'display_name': 'Committee Voting',
                'description': 'Enable/disable voting within committees',
                'category': 'committees',
                'is_enabled': True
            },
            {
                'name': 'committee_documents',
                'display_name': 'Committee Documents',
                'description': 'Enable/disable document management in committees',
                'category': 'committees',
                'is_enabled': True
            },

            # Documents
            {
                'name': 'chapter_documents',
                'display_name': 'Chapter Documents',
                'description': 'Enable/disable chapter document library',
                'category': 'documents',
                'is_enabled': True
            },
            {
                'name': 'document_versioning',
                'display_name': 'Document Versioning',
                'description': 'Enable/disable document version tracking',
                'category': 'documents',
                'is_enabled': True
            },

            # Events & Calendar
            {
                'name': 'event_attendance',
                'display_name': 'Event Attendance Tracking',
                'description': 'Enable/disable attendance tracking for events',
                'category': 'events',
                'is_enabled': True
            },
            {
                'name': 'excuse_system',
                'display_name': 'Excuse Request System',
                'description': 'Allow members to submit excuse requests for events',
                'category': 'events',
                'is_enabled': True
            },
            {
                'name': 'ical_export',
                'display_name': 'iCal Export',
                'description': 'Allow calendar export to iCal format',
                'category': 'events',
                'is_enabled': True
            },

            # Communications
            {
                'name': 'chat_channels',
                'display_name': 'Chat Channels',
                'description': 'Enable/disable the chat channel system',
                'category': 'communications',
                'is_enabled': True
            },
            {
                'name': 'email_notifications',
                'display_name': 'Email Notifications',
                'description': 'Enable/disable email notifications',
                'category': 'communications',
                'is_enabled': True
            },

            # Admin Features
            {
                'name': 'kai_reports',
                'display_name': 'KAI Reports',
                'description': 'Enable/disable the KAI reporting system',
                'category': 'admin',
                'is_enabled': True
            },
            {
                'name': 'activity_logs',
                'display_name': 'Activity Logs',
                'description': 'Enable/disable activity logging and viewing',
                'category': 'admin',
                'is_enabled': True
            },
            {
                'name': 'login_as_user',
                'display_name': 'Login As User',
                'description': 'Allow admins to login as other users',
                'category': 'admin',
                'is_enabled': True
            },
        ]

        created_flags = 0
        updated_flags = 0
        for flag_data in feature_flags:
            flag, created = FeatureFlag.objects.get_or_create(
                name=flag_data['name'],
                defaults={
                    'display_name': flag_data['display_name'],
                    'description': flag_data['description'],
                    'category': flag_data['category'],
                    'is_enabled': flag_data['is_enabled']
                }
            )
            if created:
                created_flags += 1
                self.stdout.write(self.style.SUCCESS(f'  ✓ Created feature flag: {flag.display_name}'))
            else:
                updated_flags += 1
                self.stdout.write(self.style.WARNING(f'  • Feature flag already exists: {flag.display_name}'))

        # Page Toggles
        page_toggles = [
            {
                'url_name': 'home',
                'display_name': 'Home Page',
                'description': 'Main dashboard home page',
                'is_enabled': True,
                'disabled_message': 'The home page is temporarily unavailable.'
            },
            {
                'url_name': 'vote',
                'display_name': 'Voting Page',
                'description': 'Main voting page for chapter legislation',
                'is_enabled': True,
                'disabled_message': 'Voting is temporarily unavailable. Please check back later.'
            },
            {
                'url_name': 'passed_legislation',
                'display_name': 'Passed Legislation',
                'description': 'View passed legislation archive',
                'is_enabled': True,
                'disabled_message': 'The legislation archive is temporarily unavailable.'
            },
            {
                'url_name': 'upload_legislation',
                'display_name': 'Upload Legislation',
                'description': 'Upload new legislation for voting',
                'is_enabled': True,
                'disabled_message': 'Legislation submission is temporarily disabled.'
            },
            {
                'url_name': 'committee_index',
                'display_name': 'Committee Directory',
                'description': 'Browse all committees',
                'is_enabled': True,
                'disabled_message': 'The committee directory is temporarily unavailable.'
            },
            {
                'url_name': 'officer_home',
                'display_name': 'Officer Dashboard',
                'description': 'Officer control panel',
                'is_enabled': True,
                'disabled_message': 'The officer dashboard is temporarily unavailable.'
            },
            {
                'url_name': 'attendance',
                'display_name': 'Attendance Page',
                'description': 'Chapter attendance tracking',
                'is_enabled': True,
                'disabled_message': 'Attendance tracking is temporarily unavailable.'
            },
            {
                'url_name': 'chapter_documents',
                'display_name': 'Chapter Documents',
                'description': 'Chapter document library',
                'is_enabled': True,
                'disabled_message': 'The document library is temporarily unavailable.'
            },
            {
                'url_name': 'announcements',
                'display_name': 'Announcements',
                'description': 'View chapter announcements',
                'is_enabled': True,
                'disabled_message': 'Announcements are temporarily unavailable.'
            },
            {
                'url_name': 'calendar',
                'display_name': 'Calendar',
                'description': 'Chapter calendar and events',
                'is_enabled': True,
                'disabled_message': 'The calendar is temporarily unavailable.'
            },
            {
                'url_name': 'chat_index',
                'display_name': 'Chat Channels',
                'description': 'Browse chat channels',
                'is_enabled': True,
                'disabled_message': 'Chat is temporarily unavailable.'
            },
            {
                'url_name': 'kai_dashboard',
                'display_name': 'KAI Dashboard',
                'description': 'KAI reporting dashboard',
                'is_enabled': True,
                'disabled_message': 'The KAI dashboard is temporarily unavailable.'
            },
            {
                'url_name': 'roberts_rules',
                'display_name': "Robert's Rules",
                'description': "Robert's Rules reference page",
                'is_enabled': True,
                'disabled_message': 'This page is temporarily unavailable.'
            },
            {
                'url_name': 'constitution_bylaws',
                'display_name': 'Constitution & Bylaws',
                'description': 'View constitution and bylaws',
                'is_enabled': True,
                'disabled_message': 'This page is temporarily unavailable.'
            },
        ]

        created_toggles = 0
        updated_toggles = 0
        for toggle_data in page_toggles:
            toggle, created = PageToggle.objects.get_or_create(
                url_name=toggle_data['url_name'],
                defaults={
                    'display_name': toggle_data['display_name'],
                    'description': toggle_data['description'],
                    'is_enabled': toggle_data['is_enabled'],
                    'disabled_message': toggle_data['disabled_message']
                }
            )
            if created:
                created_toggles += 1
                self.stdout.write(self.style.SUCCESS(f'  ✓ Created page toggle: {toggle.display_name}'))
            else:
                updated_toggles += 1
                self.stdout.write(self.style.WARNING(f'  • Page toggle already exists: {toggle.display_name}'))

        self.stdout.write(self.style.SUCCESS(f'\n✅ Seeding complete!'))
        self.stdout.write(self.style.SUCCESS(f'   Feature Flags: {created_flags} created, {updated_flags} already existed'))
        self.stdout.write(self.style.SUCCESS(f'   Page Toggles: {created_toggles} created, {updated_toggles} already existed'))
