#!/bin/bash
#
# Reset all user passwords except specified IDs
# Usage: ./reset_all_passwords.sh 73,72,67
# Or: ./reset_all_passwords.sh 73,72,67 MyTempPassword123
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_step() {
    echo -e "${BLUE}[STEP]${NC} $1"
}

echo "========================================="
echo "Parliament Password Reset Utility"
echo "========================================="
echo ""

# Check if running from correct directory
if [ ! -f "manage.py" ]; then
    print_error "This script must be run from the Parliament project root directory"
    print_error "Expected to find manage.py in current directory"
    exit 1
fi

# Parse arguments
EXCLUDE_IDS="${1:-}"

if [ -z "$EXCLUDE_IDS" ]; then
    print_error "Usage: $0 <comma-separated-user-ids>"
    echo ""
    echo "Password format: [first letter of first name][last name][user_id]"
    echo "Example: Mason Kimball (ID 73) will get password 'mkimball73'"
    echo ""
    echo "Examples:"
    echo "  $0 73,72,67    # Reset all except IDs 73, 72, 67"
    echo "  $0 1           # Reset all except ID 1"
    echo ""
    exit 1
fi

print_warning "This will reset passwords for ALL users except: $EXCLUDE_IDS"
print_warning "Password format: [first letter of first name][last name][user_id]"
echo ""

# Run dry-run first to show what will happen
print_step "Step 1: Checking which users will be affected (dry run)..."
echo ""

DJANGO_SETTINGS_MODULE=Parliament.settings_postgres python manage.py reset_all_passwords \
    --exclude "$EXCLUDE_IDS" \
    --dry-run

echo ""
print_warning "The above users will have their passwords reset."
echo ""

# Ask for confirmation
read -p "Do you want to proceed with the password reset? (yes/no): " -r
echo
if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
    print_error "Password reset cancelled"
    exit 0
fi

echo ""
print_step "Step 2: Resetting passwords..."
echo ""

# Run actual reset
DJANGO_SETTINGS_MODULE=Parliament.settings_postgres python manage.py reset_all_passwords \
    --exclude "$EXCLUDE_IDS"

echo ""
print_status "Password reset complete!"
echo ""
print_warning "IMPORTANT: Notify users of the password format:"
print_warning "  Password = [first letter of first name][last name][user_id]"
print_warning "  Example: Mason Kimball (ID 73) → mkimball73"
print_warning "Users will be forced to change their password on next login"
echo ""
