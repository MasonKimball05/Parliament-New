"""
Minimal Django settings for CI/CD pipelines.
All sensitive values are read from environment variables — safe to commit.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'ci-insecure-key-not-for-production')
DEBUG = os.getenv('DJANGO_DEBUG', 'True') == 'True'
ALLOWED_HOSTS = os.getenv('ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')

AUTH_USER_MODEL = 'src.ParliamentUser'

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django_otp',
    'django_otp.plugins.otp_totp',
    'src.apps.SrcConfig',
    'django_celery_beat',
]

MIDDLEWARE = [
    'src.middleware.performance.PerformanceMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'src.middleware.security.InputSanitizationMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'src.middleware.security.PasswordResetRateLimitMiddleware',
    'src.middleware.security.LoginRateLimitMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django_otp.middleware.OTPMiddleware',
    'src.middleware.two_factor.Enforce2FAMiddleware',
    'src.middleware.session_tracking.SessionTrackingMiddleware',
    'src.middleware.lockdown.EmergencyLockdownMiddleware',
    'src.middleware.security.AdminAccessMonitoringMiddleware',
    'src.middleware.security.ForcePasswordChangeMiddleware',
    'src.middleware.security.QuarantineEnforcementMiddleware',
    'src.middleware.maintenance.MaintenanceModeMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'src.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'src.context_processors.feature_flags',
                'src.context_processors.user_preferences',
                'src.context_processors.notifications',
                'src.context_processors.maintenance_mode',
            ],
        },
    },
]

DATABASES = {
    'default': {
        'ENGINE': os.getenv('DB_ENGINE', 'django.db.backends.postgresql'),
        'NAME': os.getenv('DB_NAME'),
        'USER': os.getenv('DB_USER'),
        'PASSWORD': os.getenv('DB_PASSWORD'),
        'HOST': os.getenv('DB_HOST', 'localhost'),
        'PORT': os.getenv('DB_PORT', '5432'),
        'OPTIONS': {
            'sslmode': os.getenv('DB_SSLMODE', 'prefer'),
            'connect_timeout': 10,
        },
        'TEST': {
            'NAME': os.getenv('DB_TEST_NAME', 'test_parliament_ci'),
        },
    }
}

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'parliament-ci-cache',
    }
}

SESSION_ENGINE = 'django.contrib.sessions.backends.db'

STATIC_URL = '/static/'
STATIC_ROOT = 'staticfiles'
STATICFILES_DIRS = [os.path.join(BASE_DIR, 'static')] if os.path.isdir(os.path.join(BASE_DIR, 'static')) else []
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

LOG_DIR = os.getenv('LOG_DIR', '/tmp')
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'file': {
            'level': 'INFO',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': os.path.join(LOG_DIR, 'django_actions.log'),
            'maxBytes': 10 * 1024 * 1024,
            'backupCount': 3,
            'formatter': 'detailed',
        },
    },
    'formatters': {
        'detailed': {
            'format': '{asctime} [{levelname}] {name}: {message}',
            'style': '{',
        },
    },
    'loggers': {
        'django': {'handlers': ['file'], 'level': 'INFO', 'propagate': True},
        'function_calls': {'handlers': ['file'], 'level': 'INFO', 'propagate': False},
        'admin_actions': {'handlers': ['file'], 'level': 'INFO', 'propagate': False},
        'security': {'handlers': ['file'], 'level': 'INFO', 'propagate': False},
        'src': {'handlers': ['file'], 'level': 'INFO', 'propagate': False},
    },
}

USE_TZ = True
TIME_ZONE = 'America/Chicago'
LANGUAGE_CODE = 'en-us'
USE_I18N = True
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/accounts/login/'
LOGOUT_URL = '/logout/'
LOGIN_REDIRECT_URL = '/'

EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
DEFAULT_FROM_EMAIL = 'noreply@am-parliament.org'
SECURITY_ALERT_EMAIL = DEFAULT_FROM_EMAIL

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_AGE = 2592000
SESSION_SAVE_EVERY_REQUEST = True
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = 'Lax'
X_FRAME_OPTIONS = 'SAMEORIGIN'
SECURE_CONTENT_TYPE_NOSNIFF = True
FILE_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 20 * 1024 * 1024

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'src.validators.CustomPasswordValidator', 'OPTIONS': {'min_length': 9}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
]

CRYPTOGRAPHY_KEY = os.getenv('ENCRYPTION_KEY', '').encode() if os.getenv('ENCRYPTION_KEY') else None

OTP_TOTP_ISSUER = 'Parliament'
OTP_LOGIN_URL = '/accounts/login/'
REQUIRE_2FA_FOR_ADMINS = False
REQUIRE_2FA_FOR_OFFICERS = False

ANYMAIL = {'BREVO_API_KEY': ''}

ALLOWED_DOCUMENT_TYPES = [
    'application/pdf',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/msword',
    'text/plain',
]

PASSWORD_RESET_TIMEOUT = 1800

VAPID_PRIVATE_KEY = os.getenv('VAPID_PRIVATE_KEY', '')
VAPID_PUBLIC_KEY = os.getenv('VAPID_PUBLIC_KEY', '')
VAPID_CLAIMS = None  # Push disabled in CI

# Celery — run tasks synchronously in tests (no worker needed)
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True  # surface exceptions immediately
CELERY_BROKER_URL = 'memory://'
CELERY_RESULT_BACKEND = 'cache+memory://'
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT = ['json']
