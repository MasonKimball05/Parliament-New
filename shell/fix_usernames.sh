#!/bin/bash
#
# Fix all usernames to standard format
# Usage: ./fix_usernames.sh 73,72,67
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
echo "Parliament Username Fix Utility"
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
    echo "Username format: [first letter of first name][last name] (lowercase)"
    echo "Example: Mason Kimball will become 'mkimball'"
    echo ""
    echo "Examples:"
    echo "  $0 73,72,67    # Fix all except IDs 73, 72, 67"
    echo "  $0 1           # Fix all except ID 1"
    echo ""
    exit 1
fi

print_warning "This will standardize usernames for ALL users except: $EXCLUDE_IDS"
print_warning "Username format: [first_initial][lastname] (lowercase)"
print_warning "Example: Mason Kimball → mkimball"
echo ""

# Run dry-run first to show what will happen
print_step "Step 1: Checking which usernames need updating (dry run)..."
echo ""

DJANGO_SETTINGS_MODULE=Parliament.settings python manage.py fix_usernames \
    --exclude "$EXCLUDE_IDS" \
    --dry-run

echo ""
print_warning "The above usernames will be updated."
echo ""

# Ask for confirmation
read -p "Do you want to proceed with updating usernames? (yes/no): " -r
echo
if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
    print_error "Username update cancelled"
    exit 0
fi

echo ""
print_step "Step 2: Updating usernames..."
echo ""

# Run actual update
DJANGO_SETTINGS_MODULE=Parliament.settings python manage.py fix_usernames \
    --exclude "$EXCLUDE_IDS"

echo ""
print_status "Username update complete!"
echo ""
print_warning "IMPORTANT: Users can now log in with their new standardized usernames"
print_warning "Format: [first_initial][lastname] (all lowercase)"
echo ""
