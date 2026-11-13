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

# JSON-only API. LP-19: callers are identified by Accounts-issued access
# tokens, verified offline with Accounts' public key (docs/microservices-plan.md §4).
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_AUTHENTICATION_CLASSES": ["reservations.authentication.AccessTokenAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "UNAUTHENTICATED_USER": None,
}


def read_key_file(setting, default_path):
    """Read a PEM key from the path in `setting`, else from `default_path`."""
    from django.core.exceptions import ImproperlyConfigured

    path = Path(os.environ.get(setting) or default_path)
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        raise ImproperlyConfigured(
            f"JWT public key not found at {path} ({setting}). "
            "Run scripts/init_dev_env.py to create local development keys."
        ) from None


# LP-19: Accounts' RS256 public key, enough to verify access tokens, never to
# create them. This service never receives the private key.
JWT_PUBLIC_KEY = read_key_file("JWT_PUBLIC_KEY_FILE", BASE_DIR.parent.parent / ".keys" / "jwt-public.pem")

# LP-19: Marketplace's stock API (LP-20). The key is the one Marketplace
# expects from "reservations"; it lives only in compose.env / .env, never Git.
MARKETPLACE_SERVICE_URL = os.environ.get("MARKETPLACE_SERVICE_URL", "http://127.0.0.1:8002").rstrip("/")
MARKETPLACE_SERVICE_KEY = os.environ.get("MARKETPLACE_SERVICE_KEY", "").strip()
# Short, so a slow Marketplace gives "Try again" quickly (plan §6, step 3).
MARKETPLACE_TIMEOUT_SECONDS = float(os.environ.get("MARKETPLACE_TIMEOUT_SECONDS", "3"))


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
