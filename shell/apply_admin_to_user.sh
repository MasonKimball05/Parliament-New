#!/bin/bash

echo "Fetching all users from the database..."
python ../manage.py shell -c "from src.models import ParliamentUser; [print(f'ID: {u.user_id}, Username: {u.username}') for u in ParliamentUser.objects.all()]"

echo ""
read -p "Enter the ID of the user to make admin: " user_id

python ../manage.py shell -c "
from src.models import ParliamentUser
from django.db import connection
try:
    user = ParliamentUser.objects.get(user_id='$user_id')
    user.is_admin = True
    user.is_officer = True
    user.save()
    print(f'✅ {user.username} has been granted admin privileges.')
except ParliamentUser.DoesNotExist:
    print('❌ User not found.')
"
