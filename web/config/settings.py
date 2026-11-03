"""
Django settings for the LastPlate web frontend.

The frontend renders pages and calls the service APIs. It owns no domain data.
Its only database holds Django's server-side session rows, so future access
and refresh tokens stay on the server and the browser holds only a random
session ID.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/topics/settings/
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Build paths inside the frontend like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent
# Values already set in the environment (Docker Compose, CI) take precedence.
load_dotenv(BASE_DIR / ".env", interpolate=False)

SERVICE_NAME = "web"

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

# Base URLs of the services this frontend calls.
SERVICE_URLS = {
    "accounts": os.environ.get("ACCOUNTS_SERVICE_URL", "http://127.0.0.1:8001"),
    "marketplace": os.environ.get("MARKETPLACE_SERVICE_URL", "http://127.0.0.1:8002"),
    "reservations": os.environ.get("RESERVATIONS_SERVICE_URL", "http://127.0.0.1:8003"),
}
SERVICE_TIMEOUT_SECONDS = 2


# Application definition
# No auth app: users are authenticated by the Accounts service, not here.

INSTALLED_APPS = [
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "frontend",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# Database: the frontend's own PostgreSQL database, used only for sessions.
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

# Sessions are stored server-side in the database above. Never switch to the
# signed_cookies engine: it would place tokens in the browser cookie.
SESSION_ENGINE = "django.contrib.sessions.backends.db"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 24  # matches the planned refresh-token lifetime
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
MESSAGE_STORAGE = "django.contrib.messages.storage.session.SessionStorage"


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "Asia/Beirut"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
