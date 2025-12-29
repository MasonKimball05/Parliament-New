"""
Optimized Gunicorn configuration for Parliament Django app
Designed for minimal RAM usage while maintaining performance
"""
import multiprocessing
import os

# Server socket - using unix socket for nginx communication
bind = "unix:/var/www/Parliament-New/parliament.sock"
backlog = 2048

# Worker processes - optimized for low memory
# Use 1 worker with threads for low-traffic applications
# Formula for higher traffic: (2 x $num_cores) + 1
workers = int(os.getenv('GUNICORN_WORKERS', '2'))

# Worker class - sync workers for maximum Django compatibility
# Note: gevent workers have thread-safety issues with Django's database connections
# Sync workers are more reliable and work properly with preload_app
worker_class = 'sync'
# worker_connections only applies to async workers like gevent

# Threading - adds threads to workers for handling concurrent requests
# Only use with sync workers, not with gevent
# threads = 2

# Worker lifecycle management
max_requests = 1000  # Recycle workers after N requests to prevent memory leaks
max_requests_jitter = 50  # Add randomness to prevent all workers restarting at once
worker_tmp_dir = '/dev/shm'  # Use shared memory for worker heartbeat (faster, less I/O)

# Timeouts
timeout = 120  # Worker timeout
graceful_timeout = 30  # Time to finish requests during graceful shutdown
keepalive = 5  # Keep connections alive for this many seconds

# Logging
accesslog = '-'  # Log to stdout
errorlog = '-'  # Log to stderr
loglevel = 'info'
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = 'parliament'

# Server mechanics
daemon = False  # Don't daemonize (let Docker/systemd handle this)
pidfile = None  # Don't create pidfile
umask = 0
user = None
group = None
tmp_upload_dir = None

# SSL (if needed in the future)
# keyfile = None
# certfile = None

# Pre-load application code before forking workers
# This shares code across workers, reducing memory usage
preload_app = True

# Reload on code changes (disable in production)
reload = os.getenv('GUNICORN_RELOAD', 'False').lower() == 'true'

def on_starting(server):
    """Called just before the master process is initialized"""
    server.log.info("Starting Gunicorn server")

def on_reload(server):
    """Called when configuration is reloaded"""
    server.log.info("Reloading Gunicorn configuration")

def when_ready(server):
    """Called just after the server is started"""
    server.log.info(f"Gunicorn ready. Workers: {workers}, Worker class: {worker_class}")

def pre_fork(server, worker):
    """Called just before a worker is forked"""
    pass

def post_fork(server, worker):
    """Called just after a worker has been forked"""
    server.log.info(f"Worker spawned (pid: {worker.pid})")

    # CRITICAL FIX: Close all database connections from the master process
    # This prevents thread-safety errors with gevent workers
    # Each worker will create its own database connections
    try:
        from django.db import connections
        connections.close_all()
        server.log.info(f"Closed inherited database connections in worker {worker.pid}")
    except Exception as e:
        server.log.error(f"Error closing database connections in worker {worker.pid}: {e}")

def pre_exec(server):
    """Called just before a new master process is forked"""
    server.log.info("Forking new master process")

def worker_int(worker):
    """Called when a worker receives an INT or QUIT signal"""
    worker.log.info("Worker received INT or QUIT signal")

def worker_abort(worker):
    """Called when a worker receives a SIGABRT signal"""
    worker.log.info("Worker received SIGABRT signal")
