#!/bin/bash
# Grandfather existing active users into the onboarding system.
#
# Run this on prod (and dev) after deploying v3.6.2+ and applying
# migration 0207. Marks all established users (non-default password
# + email set) as onboarding_complete=True with checklist dismissed
# so they never see the wizard or Getting Started checklist.
#
# Usage: bash grandfather_onboarding.sh

python manage.py shell -c "
from src.models import ParliamentUser

ALL_PAGES = [
    'profile', 'preferences', 'announcements', 'directory',
    'vote', 'committees', 'my_excuses', 'service_hours',
    'chats', 'calendar', 'chapter_documents',
]

updated = ParliamentUser.objects.filter(
    has_default_password=False,
    email__isnull=False,
).exclude(email='').update(
    onboarding_complete=True,
    onboarding_data={
        'pages_visited': ALL_PAGES,
        'checklist_dismissed': True,
    },
)
print(f'Grandfathered {updated} established users — they will no longer see the onboarding wizard or checklist.')
"
