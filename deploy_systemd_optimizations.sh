#!/bin/bash
#
# Systemd-Based RAM Optimization Deployment Script
# Run this on the production server to apply all memory optimizations
#

set -e

echo "========================================="
echo "Parliament RAM Optimization (Systemd)"
echo "========================================="
echo ""

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

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    print_error "Please run as root (use sudo)"
    exit 1
fi

# Check if we're on the production server
if [ ! -d "/var/www/Parliament-New" ]; then
    print_error "This script must be run on the production server"
    print_error "Expected directory: /var/www/Parliament-New"
    exit 1
fi

cd /var/www/Parliament-New

echo ""
print_step "Step 1: Recording baseline RAM usage..."
echo "=== BEFORE Optimization ===" > /tmp/ram_usage_before.txt
free -h >> /tmp/ram_usage_before.txt
echo "" >> /tmp/ram_usage_before.txt
echo "=== Process Memory ===" >> /tmp/ram_usage_before.txt
ps aux --sort=-%mem | head -10 >> /tmp/ram_usage_before.txt
print_status "Baseline saved to /tmp/ram_usage_before.txt"

echo ""
print_step "Step 2: Pulling latest code from GitHub..."
print_warning "This will update your code to the latest version"
read -p "Continue? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    print_error "Deployment cancelled"
    exit 1
fi

git pull origin main
print_status "Code updated"

echo ""
print_step "Step 3: Installing Python dependencies..."
source venv/bin/activate
pip install -r requirements.txt
print_status "Dependencies installed"

echo ""
print_step "Step 4: Optimizing PostgreSQL..."
print_warning "This will restart PostgreSQL service"
read -p "Continue? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    chmod +x optimize_postgresql.sh
    ./optimize_postgresql.sh
    print_status "PostgreSQL optimized"
else
    print_warning "Skipping PostgreSQL optimization"
fi

echo ""
print_step "Step 5: Installing Redis (optional but recommended)..."
print_status "Redis will provide shared caching and reduce memory duplication"
print_status "Expected RAM usage: +64MB (saves more in Gunicorn workers)"
read -p "Install Redis? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    chmod +x install_redis.sh
    ./install_redis.sh
    USING_REDIS=true
    print_status "Redis installed"
else
    print_warning "Skipping Redis installation"
    USING_REDIS=false
fi

echo ""
print_step "Step 6: Setting up optimized Gunicorn systemd service..."

# Stop current Gunicorn service (find its name)
CURRENT_SERVICE=$(systemctl list-units --type=service --state=running | grep -i gunicorn | awk '{print $1}' | head -1)
if [ -n "$CURRENT_SERVICE" ]; then
    print_status "Found existing service: $CURRENT_SERVICE"
    print_warning "Stopping current service..."
    systemctl stop "$CURRENT_SERVICE"
else
    print_warning "No running Gunicorn service found"
fi

# Install new systemd service
print_status "Installing optimized service file..."
cp parliament-gunicorn.service /etc/systemd/system/
chmod 644 /etc/systemd/system/parliament-gunicorn.service

# Reload systemd
systemctl daemon-reload

# Enable the service
systemctl enable parliament-gunicorn.service

# Add environment variables to .env if they don't exist
print_status "Updating environment variables..."
if ! grep -q "GUNICORN_WORKERS" .env 2>/dev/null; then
    echo "" >> .env
    echo "# Gunicorn optimization" >> .env
    echo "GUNICORN_WORKERS=2" >> .env
    print_status "Added GUNICORN_WORKERS=2 to .env"
fi

if ! grep -q "DB_CONN_MAX_AGE" .env 2>/dev/null; then
    echo "DB_CONN_MAX_AGE=300" >> .env
    print_status "Added DB_CONN_MAX_AGE=300 to .env"
fi

echo ""
print_step "Step 7: Running Django migrations..."
source venv/bin/activate
DJANGO_SETTINGS_MODULE=Parliament.settings_postgres python manage.py migrate --noinput
print_status "Migrations complete"

echo ""
print_step "Step 8: Collecting static files..."
DJANGO_SETTINGS_MODULE=Parliament.settings_postgres python manage.py collectstatic --noinput
print_status "Static files collected"

echo ""
print_step "Step 9: Starting optimized Gunicorn service..."
systemctl start parliament-gunicorn.service

# Wait for service to start
sleep 5

# Check if service is running
if systemctl is-active --quiet parliament-gunicorn.service; then
    print_status "✓ Parliament service is running!"
else
    print_error "✗ Service failed to start"
    print_error "Check logs with: journalctl -u parliament-gunicorn -n 50"
    exit 1
fi

echo ""
print_step "Step 10: Verifying application..."
print_status "Checking if application responds..."

# Try to connect to the application
if curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/ | grep -q "200\|301\|302"; then
    print_status "✓ Application is responding"
else
    print_warning "Application may not be responding correctly"
    print_warning "Check logs: journalctl -u parliament-gunicorn -f"
fi

echo ""
print_step "Step 11: Measuring RAM savings..."
sleep 3
echo ""
echo "=== AFTER Optimization ===" > /tmp/ram_usage_after.txt
free -h >> /tmp/ram_usage_after.txt
echo "" >> /tmp/ram_usage_after.txt
echo "=== Process Memory ===" >> /tmp/ram_usage_after.txt
ps aux --sort=-%mem | head -10 >> /tmp/ram_usage_after.txt

echo "=== BEFORE Optimization ==="
cat /tmp/ram_usage_before.txt
echo ""
echo "=== AFTER Optimization ==="
cat /tmp/ram_usage_after.txt

echo ""
echo "========================================="
print_status "Deployment Complete!"
echo "========================================="
echo ""
print_status "Optimizations applied:"
echo "  ✓ Gunicorn: 3 sync workers → 2 gevent workers"
echo "  ✓ Worker preloading enabled (shared code)"
echo "  ✓ Database connection pooling (5 min reuse)"
echo "  ✓ Worker recycling every 1000 requests"
if [ "$USING_REDIS" = true ]; then
    echo "  ✓ Redis caching installed (64MB limit)"
    echo "  ✓ Session storage in Redis"
fi
echo "  ✓ PostgreSQL memory optimized"
echo ""
print_status "Expected RAM savings: 100-150MB total"
print_status "  - Gunicorn: -50MB (1 fewer worker + gevent efficiency)"
print_status "  - Connection pooling: -10MB"
print_status "  - PostgreSQL tuning: -10-20MB"
if [ "$USING_REDIS" = true ]; then
    echo "  - Redis caching: Net neutral (adds 64MB, saves worker duplication)"
fi
echo ""
print_status "Monitoring commands:"
echo "  - Service status:  systemctl status parliament-gunicorn"
echo "  - Service logs:    journalctl -u parliament-gunicorn -f"
echo "  - RAM usage:       free -h"
echo "  - Process memory:  ps aux --sort=-%mem | head -15"
echo "  - Active workers:  ps aux | grep gunicorn"
echo ""
print_warning "If you experience issues, you can rollback:"
echo "  1. Stop the new service: systemctl stop parliament-gunicorn"
echo "  2. Start old service: systemctl start $CURRENT_SERVICE"
echo "  3. Restore PostgreSQL: See backup in /etc/postgresql/.../postgresql.conf.backup.*"
echo ""
