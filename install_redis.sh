#!/bin/bash
#
# Redis Installation and Setup Script
# Optional but recommended for shared caching across Gunicorn workers
#

set -e

echo "========================================="
echo "Redis Installation for Parliament"
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

# Check if Redis is already installed
if command -v redis-server &> /dev/null; then
    print_warning "Redis is already installed!"
    redis-server --version
    echo ""
    read -p "Do you want to reconfigure it? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_status "Skipping installation"
        exit 0
    fi
fi

print_status "Installing Redis server..."
apt-get update
apt-get install -y redis-server

print_status "Configuring Redis for low memory usage..."

# Backup original config
REDIS_CONF="/etc/redis/redis.conf"
BACKUP_FILE="${REDIS_CONF}.backup.$(date +%Y%m%d_%H%M%S)"
cp "$REDIS_CONF" "$BACKUP_FILE"
print_status "Backup saved to: $BACKUP_FILE"

# Update Redis configuration for memory efficiency
cat > /etc/redis/redis.conf.d/parliament.conf <<'EOF'
# Parliament-specific Redis configuration
# Optimized for low memory usage (64MB limit)

# Bind to localhost only for security
bind 127.0.0.1 ::1

# Maximum memory limit
maxmemory 64mb

# Eviction policy: Remove least recently used keys when memory limit is reached
maxmemory-policy allkeys-lru

# Disable persistence (we use this as a cache, not a database)
save ""
appendonly no

# Optimize memory usage
# Use smaller hash tables
hash-max-ziplist-entries 512
hash-max-ziplist-value 64

# Compress lists
list-max-ziplist-size -2
list-compress-depth 0

# Compress sets
set-max-intset-entries 512

# Compress sorted sets
zset-max-ziplist-entries 128
zset-max-ziplist-value 64

# Reduce number of databases (we only use db 0)
databases 2

# Timeout idle clients (5 minutes)
timeout 300

# Reduce TCP backlog
tcp-backlog 128

# Disable some features we don't need
activedefrag no

# Logging
loglevel notice
logfile /var/log/redis/redis-server.log
EOF

print_status "Redis configuration updated"

# Enable and restart Redis
print_status "Enabling Redis service..."
systemctl enable redis-server
systemctl restart redis-server

# Wait for Redis to start
sleep 2

# Verify Redis is running
if systemctl is-active --quiet redis-server; then
    print_status "Redis is running successfully!"
    echo ""
    print_status "Redis info:"
    redis-cli info memory | grep "used_memory_human"
    redis-cli info server | grep "redis_version"
else
    print_error "Redis failed to start!"
    exit 1
fi

echo ""
print_status "Installing Python Redis libraries..."
cd /var/www/Parliament-New
source venv/bin/activate
pip install redis==5.2.1 django-redis==5.4.0

echo ""
print_status "Updating .env file with Redis URL..."
if ! grep -q "REDIS_URL" /var/www/Parliament-New/.env 2>/dev/null; then
    echo "" >> /var/www/Parliament-New/.env
    echo "# Redis cache" >> /var/www/Parliament-New/.env
    echo "REDIS_URL=redis://127.0.0.1:6379/0" >> /var/www/Parliament-New/.env
    print_status "Added REDIS_URL to .env"
else
    print_warning "REDIS_URL already exists in .env"
fi

echo ""
print_status "Redis installation complete!"
echo ""
print_status "Benefits:"
echo "  - Shared cache across all Gunicorn workers"
echo "  - Reduced memory duplication"
echo "  - Session storage in Redis (faster than database)"
echo "  - Limited to 64MB RAM usage"
echo ""
print_status "Redis will be used automatically when you restart Parliament"
print_warning "Remember to restart Parliament service after this:"
echo "  sudo systemctl restart parliament-gunicorn"
echo ""
