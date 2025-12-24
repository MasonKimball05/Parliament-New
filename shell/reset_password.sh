#!/bin/bash

# Reset user password to default format and force password change
# Usage: ./reset_password.sh <user_id> [custom_password]
# Example: ./reset_password.sh 73
# Example with custom password: ./reset_password.sh 73 MyPassword123!

# Colors for output
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if user_id argument is provided
if [ -z "$1" ]; then
    echo -e "${RED}Error: User ID is required${NC}"
    echo "Usage: $0 <user_id> [custom_password]"
    echo "Example: $0 73"
    echo "Example with custom password: $0 73 MyPassword123!"
    exit 1
fi

USER_ID=$1
CUSTOM_PASSWORD=$2

echo -e "${YELLOW}Resetting password for user ID: $USER_ID${NC}"

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
# Go to the parent directory (project root)
PROJECT_DIR="$( cd "$SCRIPT_DIR/.." && pwd )"

# Check for virtual environment (development uses .venv, production uses venv)
if [ -f "$PROJECT_DIR/.venv/bin/python3" ]; then
    PYTHON="$PROJECT_DIR/.venv/bin/python3"
elif [ -f "$PROJECT_DIR/venv/bin/python3" ]; then
    PYTHON="$PROJECT_DIR/venv/bin/python3"
else
    echo -e "${RED}Error: Python virtual environment not found${NC}"
    echo "Expected: $PROJECT_DIR/.venv/bin/python3 OR $PROJECT_DIR/venv/bin/python3"
    exit 1
fi

# Build the command
if [ -z "$CUSTOM_PASSWORD" ]; then
    # No custom password provided, use auto-generated format
    CMD="cd $PROJECT_DIR && $PYTHON manage.py reset_user_password $USER_ID"
else
    # Custom password provided
    CMD="cd $PROJECT_DIR && $PYTHON manage.py reset_user_password $USER_ID --password \"$CUSTOM_PASSWORD\""
fi

# Execute the command
eval $CMD

# Check if command was successful
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ Password reset successful!${NC}"
    echo -e "${YELLOW}The user will be forced to change their password on next login.${NC}"
else
    echo -e "${RED}✗ Password reset failed${NC}"
    exit 1
fi
