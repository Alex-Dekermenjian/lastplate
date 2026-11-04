
"""Build browser-accessible listing photo URLs for local development."""

from urllib.parse import urljoin, urlsplit

from django.conf import settings


def photo_url(path):
    """Resolve a Marketplace media path to a browser-accessible URL."""
    if not path:
        return None

    # Never accept an arbitrary external URL from listing data.
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc:
        return None

    # Only Marketplace media paths are permitted.
    if not path.startswith("/media/") or path.startswith("//"):
        return None

    # Development-only: Web and Marketplace run on separate local ports.
    if not settings.DEBUG:
        return None

    base = settings.MARKETPLACE_PUBLIC_URL.rstrip("/") + "/"
    base_host = urlsplit(base).hostname

    # A Docker-internal hostname is not accessible from the browser.
    if base_host not in ("127.0.0.1", "localhost"):
        return None

    return urljoin(base, path.lstrip("/"))
