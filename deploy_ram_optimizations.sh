#!/bin/bash
#
# Deployment Script for RAM Optimizations
# Run this script to deploy memory-efficient configuration to production
#

set -e  # Exit on error

echo "========================================="
echo "Parliament RAM Optimization Deployment"
echo "========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if running on the server
if [ ! -d "/var/www/Parliament-New" ]; then
    print_error "This script must be run on the production server"
    print_error "Please run: ssh root@167.99.115.182"
    exit 1
fi

cd /var/www/Parliament-New

print_status "Step 1: Backing up current configuration..."
cp docker-compose.yml docker-compose.yml.backup.$(date +%Y%m%d_%H%M%S) || true
cp Parliament/settings_postgres.py Parliament/settings_postgres.py.backup.$(date +%Y%m%d_%H%M%S) || true

print_status "Step 2: Pulling latest code from repository..."
git pull origin main

print_status "Step 3: Building new Docker images..."
docker-compose build --no-cache

print_status "Step 4: Stopping current containers..."
docker-compose down

print_status "Step 5: Starting optimized containers..."
docker-compose up -d

print_status "Step 6: Waiting for services to be healthy..."
sleep 10

# Check if containers are running
print_status "Step 7: Verifying container status..."
docker-compose ps

# Run database migrations
print_status "Step 8: Running database migrations..."
docker-compose exec -T web python manage.py migrate --noinput

# Collect static files
print_status "Step 9: Collecting static files..."
docker-compose exec -T web python manage.py collectstatic --noinput

# Show memory usage
print_status "Step 10: Checking new memory usage..."
echo ""
echo "=== Before Optimization (if available from logs) ==="
cat ram_usage_before.txt 2>/dev/null || echo "No baseline available"
echo ""
echo "=== After Optimization ==="
free -h
echo ""
docker stats --no-stream
echo ""

print_status "Deployment complete!"
echo ""
print_status "Next steps:"
echo "  1. Monitor the application: docker-compose logs -f"
echo "  2. Check RAM usage: free -h"
echo "  3. View container stats: docker stats"
echo ""
print_warning "If you experience any issues, rollback with:"
echo "  docker-compose down"
echo "  git checkout HEAD~1"
echo "  docker-compose up -d"
echo ""
