"""
Parliament — unified settings (v3.12.0).

Single settings module replacing base_settings.py / settings_postgres.py /
settings_sqlite.py. Everything machine-specific comes from .env (see
.env.example). Key switches:

    DJANGO_DEBUG=True   -> development mode (relaxed key requirements, no SSL)
    DB_BACKEND=sqlite   -> zero-config local SQLite DB (replaces settings_sqlite)
    REDIS_URL=...       -> enables Redis cache/sessions/channels/celery

DJANGO_SETTINGS_MODULE should be 'Parliament.settings'.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(os.path.join(BASE_DIR, '.env'))

# SECURITY: Restrict allowed hosts - configure in .env file
# Example: ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com,localhost,127.0.0.1
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

DEBUG = os.getenv('DJANGO_DEBUG', os.getenv('DEBUG', 'False')) == 'True'

# SECURITY: Secret key MUST be set in environment variables for production
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY') or os.getenv('SECRET_KEY')
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = 'dev-only-insecure-secret-key-change-in-production'
    else:
        raise ValueError("DJANGO_SECRET_KEY (or SECRET_KEY) must be set in production environment")

LOGIN_URL = '/accounts/login/'

LOGOUT_URL = '/logout/'
LOGIN_REDIRECT_URL = '/'

USE_TZ = os.getenv('USE_TZ', 'True') == 'True'
TIME_ZONE = os.getenv('TIME_ZONE', 'America/Chicago')  # Central Time (CST/CDT)


AUTH_USER_MODEL = os.getenv('DJANGO_AUTH_USER_MODEL', 'src.ParliamentUser')

AUTHENTICATION_BACKENDS = [
    # ModelBackend, but the per-request session-user load skips the profile-only
    # columns (bio, JSON lists, socials, house). ParliamentUser is a wide table
    # and every authenticated request was reading all of it; those fields are
    # only used by profile/directory/house_map. See src/auth_backends.py.
    'src.auth_backends.DeferredProfileModelBackend',

    # Stock ModelBackend is kept listed ON PURPOSE. Django writes the backend's
    # dotted path into the session at login and, on every later request, logs
    # the user out if that path is no longer in this list. Dropping it would
    # therefore sign out every live session the moment this deploys, and would
    # break any `login(..., backend=...)` call still naming the old path. It
    # never wins a login (the deferring backend is tried first); it exists so
    # the transition is not a mass logout. Safe to remove a release or two
    # after this ships, once no session predates it.
    'django.contrib.auth.backends.ModelBackend',
]


# Set LOG_TO_CONSOLE=True in .env to echo logs to stdout (useful for Docker/containers)
_LOG_TO_CONSOLE = os.getenv('LOG_TO_CONSOLE', 'False') == 'True'
_LOG_HANDLERS = ['file', 'console'] if _LOG_TO_CONSOLE else ['file']

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'detailed': {
            'format': '{asctime} [{levelname}] {name}: {message}',
            'style': '{',
        },
        'console': {
            # Shorter format for terminal readability
            'format': '{asctime} [{levelname}] {name}: {message}',
            'style': '{',
            'datefmt': '%H:%M:%S',
        },
    },
    'handlers': {
        'file': {
            'level': 'INFO',
            # Use RotatingFileHandler to prevent log file growth
            'class': 'logging.handlers.RotatingFileHandler',
            'formatter': 'detailed',
            'filename': os.path.join(BASE_DIR, os.getenv('LOG_DIR', 'logs'), os.getenv('LOG_FILE_NAME' ,'django_actions.log')),
            'maxBytes': 10 * 1024 * 1024,  # 10 MB per file
            'backupCount': 3,  # Keep 3 old log files
            'encoding': 'utf-8',
        },
        'console': {
            'level': 'WARNING',  # Only WARNING+ to stdout to reduce noise
            'class': 'logging.StreamHandler',
            'formatter': 'console',
        },
    },
    'loggers': {
        'django': {
            'handlers': _LOG_HANDLERS,
            'level': 'INFO',
            'propagate': True,
        },
        'function_calls': {
            'handlers': _LOG_HANDLERS,
            'level': 'INFO',
            'propagate': False,
        },
        'admin_actions': {
            'handlers': _LOG_HANDLERS,
            'level': 'INFO',
            'propagate': False,
        },
        'security': {
            'handlers': _LOG_HANDLERS,
            'level': 'INFO',
            'propagate': False,
        },
        'src': {
            'handlers': _LOG_HANDLERS,
            'level': 'INFO',
            'propagate': False,
        },
    },
}

INSTALLED_APPS = [
    'daphne',                # Must be first — overrides runserver with ASGI handler
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django_otp',  # Two-Factor Authentication
    'django_otp.plugins.otp_totp',  # TOTP (Time-based One-Time Password)
    'django_otp.plugins.otp_static',  # Static backup codes for 2FA recovery
    'src.apps.SrcConfig',  # Use AppConfig to ensure ready() is called for signals
    'django_celery_beat',  # Periodic task scheduling (Beat scheduler stores schedules in DB)
    'rest_framework',       # Django REST Framework (3.0.0 API layer)
    'channels',             # WebSocket support (3.0.0 chat upgrade)
]

ASGI_APPLICATION = 'Parliament.asgi.application'

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'src.middleware.performance.PerformanceMiddleware',  # Track request performance metrics
    'django.contrib.sessions.middleware.SessionMiddleware',  # Required
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'src.middleware.security.PasswordResetRateLimitMiddleware',  # Rate limit password reset attempts
    'src.middleware.security.LoginRateLimitMiddleware',  # Rate limit login attempts and prevent brute force
    'django.contrib.auth.middleware.AuthenticationMiddleware',  # Required
    'src.middleware.security.InputSanitizationMiddleware',  # Detect/block SQL injection, XSS, add security headers — must be after AuthenticationMiddleware so request.user is available
    'django_otp.middleware.OTPMiddleware',  # Required for 2FA - adds is_verified() method
    'src.middleware.two_factor.Enforce2FAMiddleware',  # Enforce 2FA based on policy
    'src.middleware.session_tracking.SessionTrackingMiddleware',  # Track active sessions for display
    'src.middleware.lockdown.EmergencyLockdownMiddleware',  # Emergency lockdown mode
    'src.middleware.security.AdminAccessMonitoringMiddleware',  # Monitor and log admin panel access
    'src.middleware.security.ForcePasswordChangeMiddleware',  # Force password change after admin reset
    'src.middleware.security.QuarantineEnforcementMiddleware',  # Log out quarantined users on every request
    'src.middleware.maintenance.MaintenanceModeMiddleware',  # Block access when maintenance mode is enabled
    'django.contrib.messages.middleware.MessageMiddleware',  # Required
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'src.middleware.geo_restriction.GeoRestrictionMiddleware',  # Block export endpoints for non-US sessions
    # Developer mode — MUST be last. Its request phase runs after everything
    # above (so request.user and request.csp_nonce exist), and its response phase
    # runs before them (so the injected panel is in the body before
    # InputSanitizationMiddleware computes the CSP header, and the panel's
    # nonce-bearing <script> is therefore allowed). Inert unless the user is on
    # ADMIN_V2_USER_IDS *and* has switched it on in preferences.
    'src.middleware.dev_mode.DevModeMiddleware',
]


ROOT_URLCONF = os.getenv('DJANGO_ROOT_URLCONF', 'src.urls')

STATIC_URL = os.getenv('DJANGO_STATIC_URL', os.getenv('STATIC_URL', '/static/'))
STATIC_ROOT = os.getenv('DJANGO_STATIC_ROOT', os.getenv('STATIC_ROOT', 'staticfiles'))

STATICFILES_DIRS = [
    os.path.join(BASE_DIR, 'static'),
]

MEDIA_URL = os.getenv('DJANGO_MEDIA_URL', os.getenv('MEDIA_URL', '/media/'))
MEDIA_ROOT = os.path.join(BASE_DIR, os.getenv('MEDIA_ROOT','media'))

# v3.14.2: default storage slugifies uploaded filenames at save time
# (spaces/quotes/non-ASCII broke Content-Disposition and X-Accel headers —
# 07-19 review). DualLocationStorage fields get the same via a shared mixin;
# files already on disk are untouched. staticfiles entry = Django default.
STORAGES = {
    'default': {'BACKEND': 'src.storage.SanitizedFileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        # APP_DIRS must be False when a custom 'loaders' list is defined.
        # app_directories.Loader inside the cached.Loader provides the same behavior —
        # it searches each installed app's templates/ folder (including django.contrib.admin).
        # Templates are parsed once per worker startup rather than on every request.
        'APP_DIRS': False,
        'OPTIONS': {
            'loaders': [
                ('django.template.loaders.cached.Loader', [
                    'django.template.loaders.filesystem.Loader',
                    'django.template.loaders.app_directories.Loader',
                ]),
            ],
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'src.context_processors.feature_flags',
                'src.context_processors.user_preferences',
                'src.context_processors.notifications',
                'src.context_processors.maintenance_mode',
                'src.context_processors.impersonation',
                'src.context_processors.two_factor_status',
            ],
        },
    },
]

# Database.
# DB_BACKEND=sqlite gives a zero-config local database (replaces the old
# settings_sqlite.py). Default is PostgreSQL, configured entirely from .env.
# (Legacy DB_ENGINE=...sqlite3 is honored for old .env files.)
_db_backend = os.getenv('DB_BACKEND', '').lower()
if _db_backend == 'sqlite' or 'sqlite' in os.getenv('DB_ENGINE', ''):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / os.getenv('SQLITE_NAME', 'db.sqlite3'),
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': os.getenv('DB_ENGINE', 'django.db.backends.postgresql'),
            'NAME': os.getenv('DB_NAME'),
            'USER': os.getenv('DB_USER'),
            'PASSWORD': os.getenv('DB_PASSWORD'),
            'HOST': os.getenv('DB_HOST', 'localhost'),
            'PORT': os.getenv('DB_PORT', '5432'),
            # Connection pooling - reuse database connections instead of creating new ones
            # This significantly reduces memory and CPU overhead
            'CONN_MAX_AGE': int(os.getenv('DB_CONN_MAX_AGE', '300')),  # 5 minutes
            'CONN_HEALTH_CHECKS': True,  # Verify connections are alive before reusing
            'OPTIONS': {
                # Enable SSL for database connections in production
                'sslmode': os.getenv('DB_SSLMODE', 'prefer'),
                # Connection pooling options
                'connect_timeout': 10,
                # Reduce memory per connection
                'options': '-c statement_timeout=30000',  # 30 second query timeout
            },
        }
    }
del _db_backend

# =============================================================================
# SECURITY SETTINGS
# =============================================================================

# Session Security
# Only require secure cookies when SSL is actually enabled
USE_HTTPS = os.getenv('SECURE_SSL_REDIRECT', 'True') == 'True' and not DEBUG
SESSION_COOKIE_SECURE = USE_HTTPS  # Only send cookies over HTTPS when SSL is enabled
SESSION_COOKIE_HTTPONLY = True  # Prevent JavaScript access to session cookies
SESSION_COOKIE_SAMESITE = 'Lax'  # CSRF protection
SESSION_COOKIE_AGE = 604800  # 7 days — slides on each request, so active users never notice
SESSION_SAVE_EVERY_REQUEST = True

# CSRF Protection
CSRF_COOKIE_SECURE = USE_HTTPS  # Only send CSRF cookie over HTTPS when SSL is enabled
CSRF_COOKIE_HTTPONLY = True  # Prevent JavaScript access
CSRF_COOKIE_SAMESITE = 'Lax'
CSRF_USE_SESSIONS = False
CSRF_FAILURE_VIEW = 'django.views.csrf.csrf_failure'

# Trusted origins for CSRF. Always include the site's own origin so that
# Cloudflare (or any proxy) stripping the Referer header doesn't cause CSRF
# failures — Django checks CSRF_TRUSTED_ORIGINS before falling back to Referer.
_site_url = os.getenv('SITE_URL', 'https://am-parliament.org')
_csrf_env  = [o for o in os.getenv('CSRF_TRUSTED_ORIGINS', '').split(',') if o]
CSRF_TRUSTED_ORIGINS = list({_site_url, *_csrf_env})
del _site_url, _csrf_env

# Set to True when the site sits behind Cloudflare. Enables CF-Connecting-IP
# header for real client IP extraction instead of the rightmost X-Forwarded-For
# (which would be Cloudflare's proxy IP, not the visitor's IP).
BEHIND_CLOUDFLARE = os.getenv('BEHIND_CLOUDFLARE', 'False') == 'True'

# Security Headers
# X-XSS-Protection is deprecated; we rely on CSP instead.
# (InputSanitizationMiddleware sets 'X-XSS-Protection: 0' explicitly.
# Django's SECURE_BROWSER_XSS_FILTER setting was removed in Django 3.0,
# so setting it here is dead config — intentionally omitted.)
SECURE_CONTENT_TYPE_NOSNIFF = True  # Prevent MIME-sniffing
X_FRAME_OPTIONS = 'SAMEORIGIN'  # Allow same-origin frames (needed for document viewer)

if not DEBUG:
    # HTTPS/SSL Settings (only in production)
    SECURE_SSL_REDIRECT = os.getenv('SECURE_SSL_REDIRECT', 'True') == 'True'
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

    # HSTS (HTTP Strict Transport Security)
    SECURE_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', '31536000'))  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

# File Upload Security
FILE_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024  # 20 MB
DATA_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024  # 20 MB
FILE_UPLOAD_PERMISSIONS = 0o644  # rw-r--r--
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o755  # rwxr-xr-x

# Password Validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'src.validators.CustomPasswordValidator',
        'OPTIONS': {
            'min_length': 9,
        }
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'src.validators.PwnedPasswordValidator',
    },
]

# Allowed file types for uploads (MIME types)
ALLOWED_DOCUMENT_TYPES = [
    'application/pdf',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',  # .docx
    'application/msword',  # .doc
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',  # .xlsx
    'application/vnd.ms-excel',  # .xls
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',  # .pptx
    'application/vnd.ms-powerpoint',  # .ppt
    # Text & data files
    'text/plain',  # .txt, .md, .log, .csv
    'text/markdown',  # .md
    'text/csv',  # .csv
    'application/csv',  # .csv (alternate)
    'application/rtf',  # .rtf
    'text/rtf',  # .rtf (alternate)
    'text/x-log',  # .log (alternate)
    'application/json',  # .json
    'application/xml',  # .xml
    'text/xml',  # .xml (alternate)
]

# IP geolocation provider base URL. Defaults to ip-api.com's free HTTP-only
# endpoint. The free tier does not support HTTPS; set this to an HTTPS URL
# (e.g. an ip-api.com Pro endpoint) to avoid sending lookups over cleartext.
GEO_API_BASE_URL = os.getenv('GEO_API_BASE_URL', 'http://ip-api.com/json/')

# Additional security settings
SECURE_REFERRER_POLICY = 'same-origin'
LANGUAGE_CODE = 'en-us'
USE_I18N = True

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Email Configuration
# For development, emails will be printed to console
# For production, configure via environment variables (supports SMTP or Brevo API)
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = os.getenv('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'True') == 'True'
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'noreply@am-parliament.org')
SECURITY_ALERT_EMAIL = os.getenv('SECURITY_ALERT_EMAIL', os.getenv('DEFAULT_FROM_EMAIL', 'noreply@am-parliament.org'))
SITE_URL = os.getenv('SITE_URL', 'https://am-parliament.org')

# Anymail (Brevo) Configuration - used when EMAIL_BACKEND is anymail.backends.brevo.EmailBackend
ANYMAIL = {
    'BREVO_API_KEY': os.getenv('BREVO_API_KEY', ''),
}

# =============================================================================
# WEB PUSH / VAPID SETTINGS
# =============================================================================
# Generate on the server (produces single-line base64url strings, safe for .env):
#
#   source .venv/bin/activate
#   python3 -c "
#   from py_vapid import Vapid
#   from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption
#   import base64
#   v = Vapid(); v.generate_keys()
#   priv = v.private_key.private_bytes(Encoding.DER, PrivateFormat.PKCS8, NoEncryption())
#   pub  = v.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
#   print('VAPID_PRIVATE_KEY=' + base64.urlsafe_b64encode(priv).decode())
#   print('VAPID_PUBLIC_KEY='  + base64.urlsafe_b64encode(pub).decode())
#   "
#
# These never change once set — rotating forces all subscribers to re-subscribe.
_vapid_private_b64 = os.getenv('VAPID_PRIVATE_KEY', '')
_vapid_public_b64  = os.getenv('VAPID_PUBLIC_KEY', '')

if _vapid_private_b64:
    import base64 as _b64
    from cryptography.hazmat.primitives.serialization import (
        Encoding as _Enc, PrivateFormat as _PF, PublicFormat as _PubF, NoEncryption as _NE
    )
    from cryptography.hazmat.primitives.asymmetric.ec import (
        EllipticCurvePrivateKey as _ECPriv, SECP256R1 as _SECP256R1
    )
    from cryptography.hazmat.backends import default_backend as _backend

    # Reconstruct PEM so pywebpush can load it
    _priv_der = _b64.urlsafe_b64decode(_vapid_private_b64 + '==')
    from cryptography.hazmat.primitives.serialization import load_der_private_key as _load_der
    _priv_key = _load_der(_priv_der, password=None, backend=_backend())
    VAPID_PRIVATE_KEY = _priv_key.private_bytes(
        _Enc.PEM, _PF.PKCS8, _NE()
    ).decode()

    VAPID_PUBLIC_KEY = _vapid_public_b64  # Raw base64url — used by the subscribe JS
    VAPID_CLAIMS = {
        'sub': f'mailto:{os.getenv("DEFAULT_FROM_EMAIL", "noreply@am-parliament.org")}',
    }
else:
    VAPID_PRIVATE_KEY = ''
    VAPID_PUBLIC_KEY = ''
    VAPID_CLAIMS = None

# Cache Configuration
# Redis provides shared caching across all workers, reducing memory usage
# Falls back to LocMemCache if Redis is not available
REDIS_URL = os.getenv('REDIS_URL', '')
if REDIS_URL and not DEBUG:
    # Production: Use Redis for shared caching across workers
    CACHES = {
        'default': {
            'BACKEND': 'django_redis.cache.RedisCache',
            'LOCATION': REDIS_URL,
            'OPTIONS': {
                'CLIENT_CLASS': 'django_redis.client.DefaultClient',
                'CONNECTION_POOL_CLASS_KWARGS': {
                    'max_connections': 50,
                    'retry_on_timeout': True,
                },
                'SOCKET_CONNECT_TIMEOUT': 5,
                'SOCKET_TIMEOUT': 5,
                # Compress cached data to save memory
                'COMPRESSOR': 'django_redis.compressors.zlib.ZlibCompressor',
                # Serialize with pickle protocol 4 (faster, more efficient)
                'SERIALIZER': 'django_redis.serializers.pickle.PickleSerializer',
            },
            'KEY_PREFIX': 'parliament',
            'TIMEOUT': 300,  # 5 minutes default timeout
        }
    }
    # Use Redis for session storage (shared across workers, survives restarts)
    SESSION_ENGINE = 'django.contrib.sessions.backends.cache'
    SESSION_CACHE_ALIAS = 'default'
else:
    # Development/Fallback: Use in-memory cache
    # WARNING: LocMemCache is per-process, each Gunicorn worker has its own cache
    # For production, strongly recommend setting REDIS_URL environment variable
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'parliament-cache',
            'TIMEOUT': 300,  # 5 minutes default - entries auto-expire
            'OPTIONS': {
                'MAX_ENTRIES': 1000,  # Reduced to save memory (was 5000)
                'CULL_FREQUENCY': 3,  # When max is hit, remove 1/3 of entries
            }
        }
    }
    # Use database sessions when Redis is not available (prevents memory growth)
    SESSION_ENGINE = 'django.contrib.sessions.backends.db'

# =============================================================================
# CHANNEL LAYERS (WebSocket message bus — 3.0.0)
# =============================================================================
# Uses Redis when available (production), in-memory for local dev (no Redis required).
# The in-memory backend does NOT work across multiple processes — fine for dev only.
_redis_url = os.getenv('REDIS_URL', '')
if _redis_url and not DEBUG:
    # Production: Redis channel layer (shared across all workers/processes)
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels_redis.core.RedisChannelLayer',
            'CONFIG': {
                'hosts': [_redis_url],
                'capacity': 1500,      # max messages queued per group
                'expiry': 60,          # seconds before unread messages expire
            },
        },
    }
else:
    # Development: in-memory layer (single-process only — fine for runserver)
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels.layers.InMemoryChannelLayer',
        },
    }

# =============================================================================
# CELERY CONFIGURATION
# =============================================================================

# Broker: use Redis when available, fall back to in-process for dev/test
_celery_broker = os.getenv('REDIS_URL', '') or 'memory://'
CELERY_BROKER_URL = _celery_broker
CELERY_RESULT_BACKEND = _celery_broker if _celery_broker != 'memory://' else 'cache+memory://'

# Serialization
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT = ['json']

# Timezone
CELERY_TIMEZONE = TIME_ZONE
CELERY_ENABLE_UTC = True

# Task reliability
CELERY_TASK_ACKS_LATE = True          # Re-queue if worker dies mid-task
CELERY_WORKER_PREFETCH_MULTIPLIER = 1  # One task at a time per worker slot (fair distribution)

# Result expiry — don't keep results forever (we don't poll them, tasks are fire-and-forget)
CELERY_RESULT_EXPIRES = 3600  # 1 hour

# Beat scheduler — store schedules in the database (manageable via admin-v2)
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'

# Test runs (manage.py test / CI): execute celery tasks synchronously in-process
# so tests don't need a broker, and task exceptions surface immediately.
# (Replaces the old standalone ci_settings.py — removed 07-05-26.)
import sys as _sys
if 'test' in _sys.argv or os.getenv('PYTEST_CURRENT_TEST'):
    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_TASK_EAGER_PROPAGATES = True

# Password Reset Settings
PASSWORD_RESET_TIMEOUT = 1800  # 30 minutes (in seconds) - shorter window for security
PASSWORD_RESET_TIMEOUT_DAYS = 0  # Deprecated, but set to 0 for clarity

# =============================================================================
# FIELD-LEVEL ENCRYPTION SETTINGS
# =============================================================================

# Cryptography key for django-cryptography
# This key is used to encrypt sensitive data at rest (usernames, emails, IPs)
# Uses AES encryption with Fernet (authenticated encryption)
# CRITICAL: Never commit this key to version control
# Generate using: python3 generate_encryption_key.py
CRYPTOGRAPHY_KEY = os.getenv('ENCRYPTION_KEY', '').encode() if os.getenv('ENCRYPTION_KEY') else None

# In production, require encryption key to be set
if not DEBUG and not CRYPTOGRAPHY_KEY:
    raise ValueError(
        "ENCRYPTION_KEY must be set in production environment. "
        "Run 'python3 generate_encryption_key.py' to generate a key."
    )

# =============================================================================
# TWO-FACTOR AUTHENTICATION SETTINGS
# =============================================================================

# OTP (One-Time Password) settings for 2FA
OTP_TOTP_ISSUER = os.getenv('OTP_TOTP_ISSUER', 'Parliament')  # Shows in authenticator app
OTP_LOGIN_URL = '/accounts/login/'

# Require 2FA for admins and officers (can be configured)
REQUIRE_2FA_FOR_ADMINS = os.getenv('REQUIRE_2FA_FOR_ADMINS', 'True') == 'True'
REQUIRE_2FA_FOR_OFFICERS = os.getenv('REQUIRE_2FA_FOR_OFFICERS', 'True') == 'True'

# =============================================================================
# REST FRAMEWORK CONFIGURATION (3.0.0 API layer)
# =============================================================================

REST_FRAMEWORK = {
    # Require a valid session — no anonymous access
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    # JSON only — no browsable API in production
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ] if not DEBUG else [
        'rest_framework.renderers.JSONRenderer',
        'rest_framework.renderers.BrowsableAPIRenderer',
    ],
    # Cursor pagination keeps large lists performant
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.CursorPagination',
    'PAGE_SIZE': 50,
    # Throttling — guests are blocked, authenticated users get a generous limit
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'user': '300/hour',
    },
}
