#!/bin/bash

echo "Fetching all member names from the database..."

python ../manage.py shell -c "
from src.models import ParliamentUser
for user in ParliamentUser.objects.all():
    print(user.name, user.username, user.id)

"