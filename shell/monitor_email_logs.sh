#!/bin/bash
# Monitor email setting logs in real-time on production

echo "Monitoring email-related logs..."
echo "Press Ctrl+C to stop"
echo "================================"
echo ""

# Try to find and tail the log file
if [ -f "/var/log/parliament/debug.log" ]; then
    tail -f /var/log/parliament/debug.log | grep --line-buffered "SET_EMAIL\|set_email\|Email"
elif [ -f "/var/log/gunicorn/access.log" ]; then
    echo "Watching gunicorn access log..."
    tail -f /var/log/gunicorn/access.log | grep --line-buffered "set_email"
else
    echo "Log file not found. Checking systemd journal..."
    journalctl -u parliament -f | grep --line-buffered "SET_EMAIL\|set_email\|Email"
fi
