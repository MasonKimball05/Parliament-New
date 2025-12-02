#!/bin/bash

# Prompt for user details
echo "Enter User ID:"
read USER_ID

echo "Enter User Name:"
read USER_NAME

echo "Enter User Type (e.g., Chair, Officer, Member):"
read USER_TYPE

# Split the name into first and last names
FIRST_NAME=$(echo "$USER_NAME" | awk '{print $1}')
LAST_NAME=$(echo "$USER_NAME" | awk '{print $NF}')

# Generate the default username (full name)
USER_USERNAME="$USER_NAME"

# Generate the default password (first letter of first name + last name)
USER_PASSWORD=$(echo "${FIRST_NAME:0:1}${LAST_NAME}" | tr '[:upper:]' '[:lower:]')

# Check if user already exists by User ID or Username
EXISTING_USER=$(python ../manage.py shell -c "
from src.models import ParliamentUser
from django.core.exceptions import ObjectDoesNotExist

try:
    user = ParliamentUser.objects.get(user_id='$USER_ID')
    print('exists')
except ObjectDoesNotExist:
    print('not exists')
")

# If user exists, exit with a message
if [ "$EXISTING_USER" = "exists" ]; then
    echo "A user with this ID already exists. Please try again with a different ID."
    exit 1
fi

# Run the Django shell to create the user with generated username and password
python ../manage.py shell <<EOF
from src.models import ParliamentUser
from django.contrib.auth.hashers import make_password

# Create the user with the data from the shell script
user = ParliamentUser(
    user_id="$USER_ID",
    name="$USER_NAME",
    member_type="$USER_TYPE",
    username="$USER_USERNAME",
    password=make_password("$USER_PASSWORD")  # Hash the password for storage
)

# Save the user to the database
user.save()

print(f"User $USER_NAME with ID $USER_ID added successfully!")
EOF
