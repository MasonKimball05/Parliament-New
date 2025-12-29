#!/bin/bash
#
# PostgreSQL Optimization Script for 1GB Server
# This script optimizes PostgreSQL for low-memory environments
#

set -e

echo "========================================="
echo "PostgreSQL Memory Optimization"
echo "========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
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

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    print_error "Please run as root (use sudo)"
    exit 1
fi

# Find PostgreSQL config file
PG_VERSION=$(psql --version | grep -oP '\d+' | head -1)
PG_CONF="/etc/postgresql/${PG_VERSION}/main/postgresql.conf"

if [ ! -f "$PG_CONF" ]; then
    print_error "PostgreSQL config file not found at $PG_CONF"
    print_error "Please update the PG_CONF variable in this script"
    exit 1
fi

print_status "Found PostgreSQL config: $PG_CONF"

# Backup current config
BACKUP_FILE="${PG_CONF}.backup.$(date +%Y%m%d_%H%M%S)"
print_status "Creating backup: $BACKUP_FILE"
cp "$PG_CONF" "$BACKUP_FILE"

print_status "Applying memory optimizations..."

# Function to update or add a PostgreSQL setting
update_setting() {
    local key=$1
    local value=$2

    if grep -q "^${key}" "$PG_CONF"; then
        # Setting exists, update it
        sed -i "s/^${key}.*/${key} = ${value}/" "$PG_CONF"
        print_status "Updated: ${key} = ${value}"
    elif grep -q "^#${key}" "$PG_CONF"; then
        # Setting is commented, uncomment and update
        sed -i "s/^#${key}.*/${key} = ${value}/" "$PG_CONF"
        print_status "Enabled: ${key} = ${value}"
    else
        # Setting doesn't exist, add it
        echo "${key} = ${value}" >> "$PG_CONF"
        print_status "Added: ${key} = ${value}"
    fi
}

# Memory settings optimized for 1GB server
# Current PostgreSQL is using ~31MB, we can keep it lean

# Shared buffers: Use 128MB (recommended 25% of RAM for small servers)
update_setting "shared_buffers" "'64MB'"

# Effective cache size: Estimate of memory for caching (50% of RAM)
update_setting "effective_cache_size" "'256MB'"

# Work memory: Memory for internal sort operations (keep low)
update_setting "work_mem" "'2MB'"

# Maintenance work memory: For VACUUM, CREATE INDEX, etc.
update_setting "maintenance_work_mem" "'32MB'"

# WAL buffers: Write-ahead log buffer
update_setting "wal_buffers" "'8MB'"

# Max connections: Reduce from default 100 to save memory
# Each connection uses ~2-3MB
update_setting "max_connections" "'30'"

# Checkpoint settings for better performance
update_setting "checkpoint_completion_target" "'0.9'"

# Statistics target
update_setting "default_statistics_target" "'100'"

# Random page cost (lower for SSD)
update_setting "random_page_cost" "'1.1'"

# Effective I/O concurrency
update_setting "effective_io_concurrency" "'200'"

# Logging settings to reduce I/O
update_setting "logging_collector" "'on'"
update_setting "log_rotation_age" "'1d'"
update_setting "log_rotation_size" "'100MB'"
update_setting "log_truncate_on_rotation" "'on'"

print_status "Configuration updated successfully!"
echo ""
print_warning "Restarting PostgreSQL service..."
systemctl restart postgresql

# Wait for PostgreSQL to start
sleep 3

# Verify PostgreSQL is running
if systemctl is-active --quiet postgresql; then
    print_status "PostgreSQL restarted successfully!"
    echo ""
    print_status "New memory usage:"
    ps aux | grep postgres | grep -v grep
    echo ""
    print_status "Current connections:"
    sudo -u postgres psql -c "SELECT count(*) as active_connections FROM pg_stat_activity;" 2>/dev/null || echo "Could not query connections"
else
    print_error "PostgreSQL failed to start!"
    print_error "Restoring backup..."
    cp "$BACKUP_FILE" "$PG_CONF"
    systemctl restart postgresql
    exit 1
fi

echo ""
print_status "Optimization complete!"
print_status "Backup saved to: $BACKUP_FILE"
echo ""
print_status "Expected RAM savings: ~10-20MB"
print_status "PostgreSQL is now optimized for low-memory environments"
echo ""
