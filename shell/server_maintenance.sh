#!/bin/bash
# Parliament Server Maintenance Script
# Run this daily via cron to prevent memory/database bloat
#
# Recommended cron entry (run at 3 AM daily):
# 0 3 * * * /var/www/Parliament-New/shell/server_maintenance.sh >> /var/log/parliament-maintenance.log 2>&1

set -e

# Configuration
PROJECT_DIR="${PROJECT_DIR:-/var/www/Parliament-New}"
VENV_DIR="${VENV_DIR:-$PROJECT_DIR/.venv}"
PYTHON="${PYTHON:-$VENV_DIR/bin/python}"
LOG_DIR="${LOG_DIR:-$PROJECT_DIR/logs}"

# Timestamp for logging
echo "=========================================="
echo "Parliament Maintenance - $(date '+%Y-%m-%d %H:%M:%S')"
echo "=========================================="

# Activate virtual environment
source "$VENV_DIR/bin/activate"
cd "$PROJECT_DIR"

# 1. Run cleanup command
echo ""
echo "[1/5] Running Django cleanup..."
$PYTHON manage.py cleanup_sessions

# 2. Clear Django sessions (built-in)
echo ""
echo "[2/5] Running Django clearsessions..."
$PYTHON manage.py clearsessions

# 3. Compress old log files
echo ""
echo "[3/5] Compressing old logs..."
if [ -d "$LOG_DIR" ]; then
    find "$LOG_DIR" -name "*.log.*" -type f ! -name "*.gz" -mtime +1 -exec gzip -f {} \;
    # Remove very old compressed logs (older than 30 days)
    find "$LOG_DIR" -name "*.log.*.gz" -type f -mtime +30 -delete
    echo "Log compression complete"
else
    echo "Log directory not found, skipping"
fi

# 4. Check memory usage
echo ""
echo "[4/5] Current memory status:"
free -m 2>/dev/null || vm_stat 2>/dev/null || echo "Memory check not available"

# 5. Optionally restart Gunicorn if memory is high
echo ""
echo "[5/5] Checking if Gunicorn restart needed..."

# Get current memory usage percentage
if command -v free &> /dev/null; then
    MEM_USED=$(free | grep Mem | awk '{print int($3/$2 * 100)}')
    echo "Memory usage: ${MEM_USED}%"

    # If memory usage > 85%, restart Gunicorn
    if [ "$MEM_USED" -gt 85 ]; then
        echo "Memory usage high (${MEM_USED}%), restarting Gunicorn..."
        sudo systemctl restart gunicorn 2>/dev/null || sudo systemctl restart parliament 2>/dev/null || echo "Could not restart Gunicorn (not running as systemd service or no sudo)"
    else
        echo "Memory usage normal, no restart needed"
    fi
else
    echo "Cannot check memory usage (free command not available)"
fi

echo ""
echo "Maintenance complete at $(date '+%Y-%m-%d %H:%M:%S')"
echo "=========================================="
