import json
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from django.conf import settings
from django.shortcuts import render

from .browse_views import home_food


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


def home(request):
    statuses = [
        fetch_service_status(name, url) for name, url in settings.SERVICE_URLS.items()
    ]
    # LP-69: the home page is where customers find food, with the search bar.
    return render(request, "frontend/home.html", {"statuses": statuses, **home_food(request)})
