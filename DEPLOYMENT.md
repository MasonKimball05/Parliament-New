# Parliament Deployment Guide

Complete guide for deploying Parliament to production.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Docker Deployment](#docker-deployment)
- [Manual Deployment](#manual-deployment)
- [Cloud Platforms](#cloud-platforms)
- [Post-Deployment](#post-deployment)
- [Maintenance](#maintenance)

---

## Prerequisites

### Server Requirements
- **OS**: Ubuntu 20.04 LTS or newer (recommended)
- **RAM**: 2GB minimum, 4GB recommended
- **CPU**: 2 cores minimum
- **Storage**: 20GB minimum
- **Domain**: Registered domain name with DNS configured

### Required Software
- Python 3.11+
- PostgreSQL 15+
- Nginx
- Gunicorn
- Git

---

## Docker Deployment

### Quick Deploy (Recommended)

1. **Install Docker and Docker Compose**
```bash
# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Install Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Verify installations
docker --version
docker-compose --version
```

2. **Clone Repository**
```bash
cd /var/www
sudo git clone https://github.com/yourusername/Parliament.git
cd Parliament
```

3. **Configure Environment**
```bash
cp .env.example .env
sudo nano .env
```

Update these critical settings:
```bash
SECRET_KEY=<generate-secure-key>
DEBUG=False
ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com
DB_PASSWORD=<secure-password>
EMAIL_HOST_USER=<your-email>
EMAIL_HOST_PASSWORD=<email-password>
```

4. **Build and Start Services**
```bash
sudo docker-compose up -d
```

5. **Initialize Database**
```bash
# Run migrations
sudo docker-compose exec web python manage.py migrate

# Restore default data
sudo docker-compose exec web python manage.py restore_committees_and_roles

# Create admin user
sudo docker-compose exec web python manage.py createsuperuser

# Collect static files
sudo docker-compose exec web python manage.py collectstatic --noinput
```

6. **Configure SSL (Let's Encrypt)**
```bash
# Install Certbot
sudo apt install certbot python3-certbot-nginx

# Get certificate
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com

# Auto-renewal is configured automatically
# Test renewal
sudo certbot renew --dry-run
```

Done! Visit https://yourdomain.com

---

## Manual Deployment

### 1. Server Setup

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install dependencies
sudo apt install -y python3.11 python3.11-venv python3-pip postgresql postgresql-contrib nginx git
```

### 2. Create Deployment User

```bash
# Create user
sudo adduser parliament
sudo usermod -aG sudo parliament

# Switch to deployment user
sudo su - parliament
```

### 3. Database Setup

```bash
# Switch to postgres user
sudo -u postgres psql

# Create database and user
CREATE DATABASE parliament_db;
CREATE USER parliament_user WITH PASSWORD 'secure_password_here';
ALTER ROLE parliament_user SET client_encoding TO 'utf8';
ALTER ROLE parliament_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE parliament_user SET timezone TO 'UTC';
GRANT ALL PRIVILEGES ON DATABASE parliament_db TO parliament_user;
\q
```

### 4. Application Setup

```bash
# Clone repository
cd /var/www
sudo git clone https://github.com/yourusername/Parliament.git
sudo chown -R parliament:parliament Parliament
cd Parliament

# Create virtual environment
python3.11 -m venv venv
source venv/bin/activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn psycopg2-binary

# Configure environment
cp .env.example .env
nano .env  # Update with production settings
```

### 5. Django Configuration

```bash
# Run migrations
python manage.py migrate

# Restore default data
python manage.py restore_committees_and_roles

# Create superuser
python manage.py createsuperuser

# Collect static files
python manage.py collectstatic --noinput

# Test the application
python manage.py runserver 0.0.0.0:8000
```

### 6. Gunicorn Setup

Create systemd service file:
```bash
sudo nano /etc/systemd/system/parliament.service
```

```ini
[Unit]
Description=Parliament Gunicorn Daemon
After=network.target

[Service]
User=parliament
Group=www-data
WorkingDirectory=/var/www/Parliament
EnvironmentFile=/var/www/Parliament/.env
ExecStart=/var/www/Parliament/venv/bin/gunicorn \
    --workers 3 \
    --bind unix:/var/www/Parliament/parliament.sock \
    --timeout 120 \
    --access-logfile /var/www/Parliament/logs/gunicorn-access.log \
    --error-logfile /var/www/Parliament/logs/gunicorn-error.log \
    Parliament.wsgi:application

[Install]
WantedBy=multi-user.target
```

Create log directory:
```bash
mkdir -p /var/www/Parliament/logs
```

Start and enable service:
```bash
sudo systemctl start parliament
sudo systemctl enable parliament
sudo systemctl status parliament
```

### 7. Nginx Configuration

```bash
sudo nano /etc/nginx/sites-available/parliament
```

```nginx
upstream parliament_app {
    server unix:/var/www/Parliament/parliament.sock fail_timeout=0;
}

server {
    listen 80;
    server_name yourdomain.com www.yourdomain.com;
    client_max_body_size 20M;

    # Logging
    access_log /var/log/nginx/parliament-access.log;
    error_log /var/log/nginx/parliament-error.log;

    # Static files
    location /static/ {
        alias /var/www/Parliament/staticfiles/;
        expires 30d;
        add_header Cache-Control "public, immutable";
    }

    # Media files
    location /media/ {
        alias /var/www/Parliament/media/;
        expires 7d;
        add_header Cache-Control "public";
    }

    # Proxy to Gunicorn
    location / {
        proxy_pass http://parliament_app;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Host $host;
        proxy_redirect off;
    }

    # Security headers
    add_header X-Frame-Options "DENY" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
}
```

Enable site:
```bash
sudo ln -s /etc/nginx/sites-available/parliament /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

### 8. SSL Certificate

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com
```

---

## Cloud Platforms

### AWS Elastic Beanstalk

1. **Install EB CLI**
```bash
pip install awsebcli
```

2. **Initialize EB**
```bash
eb init -p python-3.11 parliament-app
```

3. **Create Environment**
```bash
eb create parliament-prod
```

4. **Configure Environment Variables**
```bash
eb setenv SECRET_KEY=xxx DEBUG=False DB_HOST=xxx DB_PASSWORD=xxx
```

5. **Deploy**
```bash
eb deploy
```

### Heroku

1. **Install Heroku CLI**
```bash
curl https://cli-assets.heroku.com/install.sh | sh
```

2. **Login and Create App**
```bash
heroku login
heroku create parliament-app
```

3. **Add PostgreSQL**
```bash
heroku addons:create heroku-postgresql:hobby-dev
```

4. **Configure**
```bash
heroku config:set SECRET_KEY=xxx
heroku config:set DEBUG=False
heroku config:set DISABLE_COLLECTSTATIC=1
```

5. **Deploy**
```bash
git push heroku main
heroku run python manage.py migrate
heroku run python manage.py createsuperuser
```

### DigitalOcean App Platform

1. **Connect Repository**
   - Go to DigitalOcean Console
   - Create new App
   - Connect GitHub repository

2. **Configure**
   - Set environment variables in console
   - Configure build command: `pip install -r requirements.txt`
   - Configure run command: `gunicorn Parliament.wsgi:application`

3. **Add Database**
   - Add PostgreSQL database component
   - Configure connection string

4. **Deploy**
   - Click "Deploy"
   - App platform handles everything automatically

---

## Post-Deployment

### 1. Verify Deployment

```bash
# Check service status
sudo systemctl status parliament
sudo systemctl status nginx
sudo systemctl status postgresql

# Check logs
sudo journalctl -u parliament -n 50
tail -f /var/www/Parliament/logs/gunicorn-error.log

# Test database connection
python manage.py dbshell
```

### 2. Security Checklist

- [ ] Set `DEBUG=False` in production
- [ ] Use strong `SECRET_KEY`
- [ ] Configure `ALLOWED_HOSTS`
- [ ] Enable HTTPS/SSL
- [ ] Set secure cookie settings
- [ ] Configure firewall (UFW)
```bash
sudo ufw allow 'Nginx Full'
sudo ufw allow OpenSSH
sudo ufw enable
```
- [ ] Regular security updates
```bash
sudo apt update && sudo apt upgrade -y
```
- [ ] Database backups configured
- [ ] Change default passwords
- [ ] Disable SSH password authentication (use keys)

### 3. Performance Optimization

**Enable Caching**
```python
# settings.py
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': 'redis://127.0.0.1:6379/1',
    }
}
```

**Database Optimization**
```sql
-- PostgreSQL tuning
ALTER DATABASE parliament_db SET shared_buffers = '256MB';
ALTER DATABASE parliament_db SET work_mem = '10MB';
ALTER DATABASE parliament_db SET maintenance_work_mem = '64MB';
```

**Nginx Gzip Compression** (already in nginx.conf)

### 4. Monitoring

**Setup Log Rotation**
```bash
sudo nano /etc/logrotate.d/parliament
```

```
/var/www/Parliament/logs/*.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    create 0640 parliament www-data
    sharedscripts
    postrotate
        systemctl reload parliament > /dev/null 2>&1
    endscript
}
```

**Install Monitoring Tools**
```bash
# Install htop for system monitoring
sudo apt install htop

# Install postgres monitoring
sudo apt install postgresql-contrib
```

---

## Maintenance

### Regular Backups

**Database Backups**
```bash
# Manual backup
sudo -u postgres pg_dump parliament_db > backup_$(date +%Y%m%d).sql

# Automated daily backups
sudo nano /etc/cron.daily/parliament-backup
```

```bash
#!/bin/bash
BACKUP_DIR="/var/backups/parliament"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR

# Database backup
sudo -u postgres pg_dump parliament_db | gzip > $BACKUP_DIR/db_$DATE.sql.gz

# Media files backup
tar -czf $BACKUP_DIR/media_$DATE.tar.gz /var/www/Parliament/media/

# Keep only last 7 days
find $BACKUP_DIR -name "*.gz" -mtime +7 -delete
```

```bash
sudo chmod +x /etc/cron.daily/parliament-backup
```

### Updates and Maintenance

**Update Application**
```bash
cd /var/www/Parliament
git pull origin main
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
sudo systemctl restart parliament
```

**Database Maintenance**
```bash
# Vacuum database
sudo -u postgres vacuumdb --all --analyze

# Reindex
sudo -u postgres reindexdb parliament_db
```

### Rollback Procedure

```bash
# 1. Stop service
sudo systemctl stop parliament

# 2. Restore database
sudo -u postgres psql
DROP DATABASE parliament_db;
CREATE DATABASE parliament_db;
\q
gunzip < /var/backups/parliament/db_YYYYMMDD.sql.gz | sudo -u postgres psql parliament_db

# 3. Restore code
cd /var/www/Parliament
git reset --hard <previous-commit>

# 4. Restart
sudo systemctl start parliament
```

---

## Troubleshooting

### Common Issues

**502 Bad Gateway**
- Check Gunicorn is running: `sudo systemctl status parliament`
- Check socket file exists: `ls -la /var/www/Parliament/parliament.sock`
- Check Nginx error logs: `sudo tail -f /var/log/nginx/error.log`

**Static Files Not Loading**
- Run: `python manage.py collectstatic --noinput`
- Check Nginx static file location matches `STATIC_ROOT`
- Verify permissions: `sudo chown -R parliament:www-data /var/www/Parliament/staticfiles`

**Database Connection Errors**
- Verify PostgreSQL is running: `sudo systemctl status postgresql`
- Check credentials in `.env`
- Test connection: `psql -U parliament_user -d parliament_db`

**Permission Denied Errors**
```bash
sudo chown -R parliament:www-data /var/www/Parliament
sudo chmod -R 755 /var/www/Parliament
sudo chmod -R 775 /var/www/Parliament/media
```

---

## Support

For deployment issues:
- Check logs: `/var/www/Parliament/logs/`
- Review Nginx logs: `/var/log/nginx/`
- Check system logs: `sudo journalctl -u parliament`

Need help? Open an issue on GitHub or contact support.
