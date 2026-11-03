"""
Django settings for the LastPlate Accounts service.

Accounts owns users, passwords, roles and login. It runs as its own process
with its own PostgreSQL database; other services never read this database.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/topics/settings/
"""

import os
from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

# Build paths inside the service like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent
# Values already set in the environment (Docker Compose, CI) take precedence.
load_dotenv(BASE_DIR / ".env", interpolate=False)

SERVICE_NAME = "accounts"

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get("DJANGO_DEBUG", "False").lower() == "true"

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
    if host.strip()
]

# Readiness responses name the connected database only when this is enabled.
HEALTH_EXPOSE_DETAILS = os.environ.get("HEALTH_EXPOSE_DETAILS", "False").lower() == "true"


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "accounts",
]
AUTH_USER_MODEL = "accounts.User"
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# JSON-only API authenticated by bearer tokens. Views deny access unless they
# explicitly allow it, as registration, sign-in and the health endpoints do.
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    # Per-client limits count the caller's address. Behind the web frontend
    # that is the frontend itself, so sign-in is also limited per email.
    "DEFAULT_THROTTLE_RATES": {
        "auth_register": "10/hour",
        "auth_sign_in": "30/minute",
        "auth_sign_in_email": "5/minute",
    },
}


def read_key_file(setting, default_path):
    """Read a PEM key from the path in `setting`, else from `default_path`."""
    path = Path(os.environ.get(setting) or default_path)
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        raise ImproperlyConfigured(
            f"JWT key not found at {path} ({setting}). "
            "Run scripts/init_dev_env.py to create local development keys."
        ) from None


# Only Accounts holds the RS256 private key and signs tokens. Other services
# get the public key alone and can verify tokens but never create them. Keys
# live outside Git and outside container images (repository-root .keys/ for
# local runs, a read-only mount in Docker Compose).
JWT_KEY_DIR = BASE_DIR.parent.parent / ".keys"

# Access tokens are short-lived; refresh tokens are blacklisted on sign-out.
SIMPLE_JWT = {
    "ALGORITHM": "RS256",
    "SIGNING_KEY": read_key_file("JWT_PRIVATE_KEY_FILE", JWT_KEY_DIR / "jwt-private.pem"),
    "VERIFYING_KEY": read_key_file("JWT_PUBLIC_KEY_FILE", JWT_KEY_DIR / "jwt-public.pem"),
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=1),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "UPDATE_LAST_LOGIN": True,
}


# Database: this service's own PostgreSQL database and database user.
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ["DB_NAME"],
        "USER": os.environ["DB_USER"],
        "PASSWORD": os.environ["DB_PASSWORD"],
        "HOST": os.environ["DB_HOST"],
        "PORT": os.environ["DB_PORT"],
        "OPTIONS": {"connect_timeout": 5},
    }
}

# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = "Asia/Beirut"

USE_I18N = True

USE_TZ = True


# Static files (Django admin assets)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
