"""
Django settings for the LastPlate Marketplace service.

Marketplace owns shops, shop approval, listings and stock. It runs as its own process
with its own PostgreSQL database and refers to data owned by other services
only by ID, never through a shared database or ForeignKey.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/topics/settings/
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

# Build paths inside the service like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent
# Values already set in the environment (Docker Compose, CI) take precedence.
load_dotenv(BASE_DIR / ".env", interpolate=False)

SERVICE_NAME = "marketplace"

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
# Users live in the Accounts service, so Django's auth and admin apps are not
# installed here. Callers will be identified by Accounts-issued tokens.

INSTALLED_APPS = [
    "rest_framework",
    "marketplace",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

WSGI_APPLICATION = "config.wsgi.application"

# JSON-only API. Callers authenticate with Accounts-issued access tokens; API
# views deny access unless they explicitly allow it, as the health endpoints do.
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_AUTHENTICATION_CLASSES": ["marketplace.authentication.AccessTokenAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "UNAUTHENTICATED_USER": None,
}


def read_key_file(setting, default_path):
    """Read a PEM key from the path in `setting`, else from `default_path`."""
    path = Path(os.environ.get(setting) or default_path)
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        raise ImproperlyConfigured(
            f"JWT public key not found at {path} ({setting}). "
            "Run scripts/init_dev_env.py to create local development keys."
        ) from None


# Accounts' RS256 public key: enough to verify access tokens, never to create
# them. This service never receives the private key.
JWT_PUBLIC_KEY = read_key_file("JWT_PUBLIC_KEY_FILE", BASE_DIR.parent.parent / ".keys" / "jwt-public.pem")


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


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "Asia/Beirut"

USE_I18N = True

USE_TZ = True

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
