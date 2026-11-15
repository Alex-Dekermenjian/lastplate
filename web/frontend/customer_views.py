"""LP-18: the customer's Search page, and a placeholder for My orders until
ordering is built."""

from django.shortcuts import render
from django.views.decorators.http import require_GET

from .account_views import customer_required
from .browse_views import home_food


@customer_required
@require_GET
def search(request):
    """The Search tab: the same Where / Type of food search as the home page
    (LP-69), on its own page. If Marketplace is down the page still opens, with
    a short message instead of food."""
    return render(request, "frontend/search.html", home_food(request))


@customer_required
def my_orders(request):
    return render(request, "frontend/coming_soon.html", {
        "heading": "My orders",
        "message": "Your orders will appear here once ordering opens.",
    })