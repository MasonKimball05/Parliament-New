#!/bin/bash
# Mark one user present by user_id
# Usage: ./mark_present.sh USERID

if [ -z "$1" ]; then
  echo "Usage: ./mark_present.sh USERID"
  exit 1
fi

USERID="$1"

cd "$(dirname "$0")/.."

./manage.py shell << EOF
from src.models import ParliamentUser, Attendance
from datetime import date

try:
    user = ParliamentUser.objects.get(user_id="${USERID}")
except ParliamentUser.DoesNotExist:
    print("User not found: ${USERID}")
    raise SystemExit

attendance, created = Attendance.objects.update_or_create(
    user=user,
    date=date.today(),
    defaults={"present": True}
)

print(f"Marked user {user.name} (${USERID}) present for today.")
EOF