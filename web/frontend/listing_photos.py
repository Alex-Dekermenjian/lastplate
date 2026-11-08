"""Listing and shop photos, served through this site (LP-46).

Marketplace stores the photos, but a visitor's browser only ever talks to this
site: through a Cloudflare tunnel, Marketplace's own port is unreachable. So
photo links are relative (/media/...) and the /media/ route below fetches the
file from Marketplace over the internal network.
"""

import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from django.conf import settings
from django.http import Http404, HttpResponse
from django.views.decorators.http import require_GET

MEDIA_PREFIX = "/media/"

# folder/name.ext from plain characters only: no "..", no hidden files, no
# query strings. Marketplace saves JPEG and PNG photos only.
MEDIA_PATH = re.compile(r"^(?:[A-Za-z0-9_-][A-Za-z0-9_.-]*/)*[A-Za-z0-9_-]+\.(jpg|jpeg|png)$", re.IGNORECASE)
CONTENT_TYPES = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}
MAX_PHOTO_BYTES = 10 * 1024 * 1024


def photo_url(path):
    """Turn a Marketplace media path into a link on this site, or None."""
    if not path:
        return None

    # Never accept an arbitrary external URL from listing data.
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        return None

    # Only Marketplace media paths are permitted.
    if not path.startswith(MEDIA_PREFIX) or not MEDIA_PATH.match(path[len(MEDIA_PREFIX):]):
        return None

    # Development-only: Marketplace serves uploaded files only when DEBUG is on.
    if not settings.DEBUG:
        return None

    # Relative, so it works on whichever host the visitor used (localhost or the tunnel).
    return path


@require_GET
def media_proxy(request, path):
    """Stream one photo from Marketplace to the browser."""
    match = MEDIA_PATH.match(path)
    if not match:
        raise Http404

    url = settings.SERVICE_URLS["marketplace"].rstrip("/") + MEDIA_PREFIX + quote(path)
    try:
        with urlopen(Request(url, method="GET"), timeout=settings.SERVICE_API_TIMEOUT_SECONDS) as upstream:
            body = upstream.read(MAX_PHOTO_BYTES + 1)
    except HTTPError as error:
        if error.code == 404:
            raise Http404 from None
        return HttpResponse(status=502)
    except (URLError, TimeoutError, OSError):
        return HttpResponse(status=502)

    if len(body) > MAX_PHOTO_BYTES:
        return HttpResponse(status=502)

    # The type comes from the checked extension, never from the upstream reply.
    response = HttpResponse(body, content_type=CONTENT_TYPES[match.group(1).lower()])
    # File names are random and never reused, so the browser may keep them.
    response["Cache-Control"] = "public, max-age=86400"
    return response
