#!/usr/bin/env python3
"""
Script to create PageToggle entries for all pages with @require_page_enabled decorator.
Run this with: DJANGO_SETTINGS_MODULE=Parliament.settings_postgres python3 manage.py shell < create_page_toggles.py
Or in Django shell: exec(open('create_page_toggles.py').read())
"""

from src.models_feature_flags import PageToggle

# Define all page toggles to create
page_toggles = [
    {
        'url_name': 'home',
        'display_name': 'Home Page',
        'description': 'Main dashboard/home page showing recent activity and quick links',
        'is_enabled': True,
        'disabled_message': 'The home page is temporarily unavailable. Please check back later.'
    },
    {
        'url_name': 'vote',
        'display_name': 'Voting Page',
        'description': 'Chapter legislation voting page',
        'is_enabled': True,
        'disabled_message': 'The voting system is temporarily unavailable. Please check back later.'
    },
    {
        'url_name': 'passed_legislation',
        'display_name': 'Passed Legislation',
        'description': 'View all passed chapter legislation',
        'is_enabled': True,
        'disabled_message': 'Passed legislation viewing is temporarily unavailable.'
    },
    {
        'url_name': 'calendar',
        'display_name': 'Calendar',
        'description': 'Event calendar with subscriptions',
        'is_enabled': True,
        'disabled_message': 'The calendar is temporarily unavailable. Please check back later.'
    },
    {
        'url_name': 'profile',
        'display_name': 'User Profile',
        'description': 'User profile page for updating personal information',
        'is_enabled': True,
        'disabled_message': 'Profile management is temporarily unavailable.'
    },
    {
        'url_name': 'chapter_documents',
        'display_name': 'Chapter Documents',
        'description': 'View documents published to the entire chapter',
        'is_enabled': True,
        'disabled_message': 'Chapter documents are temporarily unavailable.'
    },
    {
        'url_name': 'committee_index',
        'display_name': 'Committee Index',
        'description': 'Main committee listing page',
        'is_enabled': True,
        'disabled_message': 'The committee index is temporarily unavailable.'
    },
    {
        'url_name': 'committee_home',
        'display_name': 'Committee Home',
        'description': 'Individual committee home pages',
        'is_enabled': True,
        'disabled_message': 'Committee home pages are temporarily unavailable.'
    },
    {
        'url_name': 'committee_documents',
        'display_name': 'Committee Documents',
        'description': 'Committee document viewing',
        'is_enabled': True,
        'disabled_message': 'Committee documents are temporarily unavailable.'
    },
    {
        'url_name': 'officer_home',
        'display_name': 'Officer Dashboard',
        'description': 'Officer home page/dashboard',
        'is_enabled': True,
        'disabled_message': 'The officer dashboard is temporarily unavailable.'
    },
    {
        'url_name': 'user_list',
        'display_name': 'User List',
        'description': 'Officer page to view and manage users',
        'is_enabled': True,
        'disabled_message': 'The user list is temporarily unavailable.'
    },
]

# Create or update each page toggle
created_count = 0
updated_count = 0
skipped_count = 0

print("\n" + "="*60)
print("Creating PageToggle Entries")
print("="*60 + "\n")

for toggle_data in page_toggles:
    url_name = toggle_data['url_name']

    try:
        toggle, created = PageToggle.objects.get_or_create(
            url_name=url_name,
            defaults=toggle_data
        )

        if created:
            print(f"✓ Created: {toggle_data['display_name']} ({url_name})")
            created_count += 1
        else:
            # Update existing toggle if fields differ
            updated = False
            for key, value in toggle_data.items():
                if key != 'url_name' and getattr(toggle, key) != value:
                    setattr(toggle, key, value)
                    updated = True

            if updated:
                toggle.save()
                print(f"↻ Updated: {toggle_data['display_name']} ({url_name})")
                updated_count += 1
            else:
                print(f"- Skipped: {toggle_data['display_name']} ({url_name}) - already exists")
                skipped_count += 1

    except Exception as e:
        print(f"✗ Error creating {url_name}: {e}")

print("\n" + "="*60)
print("Summary:")
print(f"  Created: {created_count}")
print(f"  Updated: {updated_count}")
print(f"  Skipped: {skipped_count}")
print(f"  Total:   {len(page_toggles)}")
print("="*60 + "\n")

# Display all current page toggles
print("\nCurrent Page Toggles in Database:")
print("-" * 60)
all_toggles = PageToggle.objects.all().order_by('display_name')
for toggle in all_toggles:
    status = "✓ Enabled" if toggle.is_enabled else "✗ Disabled"
    print(f"  [{status}] {toggle.display_name} ({toggle.url_name})")
print("-" * 60)
print(f"Total Page Toggles: {all_toggles.count()}\n")
