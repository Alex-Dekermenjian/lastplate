import json
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from django.conf import settings
from django.http import Http404
from django.shortcuts import render


def fetch_service_status(name, base_url):
    """Call one service's readiness API and summarise the answer."""
    try:
        with urlopen(
            f"{base_url}/health/ready/", timeout=settings.SERVICE_TIMEOUT_SECONDS
        ) as response:
            body = json.load(response)
    except HTTPError as error:
        return {"name": name, "state": "not ready", "detail": f"HTTP {error.code}"}
    except (URLError, TimeoutError, ValueError):
        return {"name": name, "state": "unreachable", "detail": "no valid response"}
    return {"name": name, "state": body.get("status", "unknown"), "detail": ""}


def demo_guide(request):
    """LP-15: One page that walks through the Sprint 1 demonstration.

    Development only: it names the fictional demo accounts and links to the
    Marketplace admin, so it must never exist on a deployed site.
    """
    if not settings.DEBUG:
        raise Http404
    marketplace = (settings.MARKETPLACE_PUBLIC_URL or settings.SERVICE_URLS["marketplace"]).rstrip("/")
    return render(
        request,
        "frontend/demo_guide.html",
        {"admin_url": f"{marketplace}/admin/marketplace/shop/"},
    )


def home(request):
    statuses = [
        fetch_service_status(name, url) for name, url in settings.SERVICE_URLS.items()
    ]
    return render(request, "frontend/home.html", {"statuses": statuses})
