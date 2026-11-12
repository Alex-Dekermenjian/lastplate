"""
Django settings for the LastPlate Reservations service.

Reservations owns reservations and their history. It runs as its own process
with its own PostgreSQL database and refers to data owned by other services
only by ID, never through a shared database or ForeignKey.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/topics/settings/
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Build paths inside the service like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent
# Values already set in the environment (Docker Compose, CI) take precedence.
load_dotenv(BASE_DIR / ".env", interpolate=False)

SERVICE_NAME = "reservations"

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
# Customers and shop operators live in the Accounts service and will be
# identified by Accounts-issued tokens. Django's auth tables here hold only
# local team admins for this service's own admin (LP-17), as in Marketplace.

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "reservations",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# Browsers share cookies across ports on one host, so these names keep an admin
# sign-in here from replacing a session of the web frontend or another admin.
SESSION_COOKIE_NAME = "reservations_admin_sessionid"
CSRF_COOKIE_NAME = "reservations_admin_csrftoken"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"


def _pickup_hold_minutes(raw):
    """Minutes an order is held after it is placed (or the window opens)."""
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        minutes = 0
    if minutes < 1:
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured("PICKUP_HOLD_MINUTES must be a whole number of minutes, at least 1.")
    return minutes


# LP-17: "pick up by" = placed time (or window opening) + this, capped at the
# window's end. 60 by default; the demo can set PICKUP_HOLD_MINUTES=2.
PICKUP_HOLD_MINUTES = _pickup_hold_minutes(os.environ.get("PICKUP_HOLD_MINUTES", "60"))

WSGI_APPLICATION = "config.wsgi.application"

# JSON-only API. Token verification is planned (see docs/microservices-plan.md);
# until then no request is authenticated and API views deny access unless they
# explicitly allow it, as the health endpoints do.
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "UNAUTHENTICATED_USER": None,
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


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "Asia/Beirut"

USE_I18N = True

USE_TZ = True

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
