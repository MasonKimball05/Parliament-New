#!/bin/bash
#
# RAM Analysis Script for Parliament Server
# Run this on the production server to analyze current memory usage
#

echo "========================================="
echo "Parliament Server RAM Analysis"
echo "========================================="
echo ""

echo "=== System Memory Overview ==="
free -h
echo ""

echo "=== Docker Container Stats ==="
docker stats --no-stream --format "table {{.Container}}\t{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"
echo ""

echo "=== Top 15 Memory-Consuming Processes ==="
ps aux --sort=-%mem | head -16
echo ""

echo "=== PostgreSQL Memory Usage ==="
docker exec parliament-db ps aux | grep postgres
echo ""

echo "=== Docker Container Details ==="
for container in $(docker ps --format '{{.Names}}'); do
    echo "--- $container ---"
    docker inspect $container --format='Memory Limit: {{.HostConfig.Memory}}'
    docker stats $container --no-stream --format "Mem Usage: {{.MemUsage}} ({{.MemPerc}})"
    echo ""
done

echo "=== Disk Usage ==="
df -h
echo ""

echo "=== Network Connections ==="
netstat -an | grep :80 | wc -l
echo "HTTP connections: $(netstat -an | grep :80 | wc -l)"
echo "Database connections: $(netstat -an | grep :5432 | wc -l)"
echo ""

echo "=== System Uptime ==="
uptime
echo ""

echo "========================================="
echo "Analysis complete!"
echo "========================================="
