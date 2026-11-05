"""LP-57: /console, where the LastPlate administrator reviews restaurants.

Only accounts with the Admin role may open these pages. Marketplace checks the
Admin token again on every call and applies the same review rules as the
Django admin, so this page is never the only check.
"""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render

from . import services
from .account_views import STATUS_EXPLANATIONS, admin_required, authenticated_call
from .browse_views import lebanon_time
from .forms import ShopDecisionForm
from .services import ServiceUnavailable

UNAVAILABLE = "We can't reach the restaurant service right now. Please try again in a few minutes."
DECISION_UNCONFIRMED = (
    "We couldn't reach the restaurant service, so we can't tell whether your decision was saved. "
    "Check the restaurant's status below before trying again."
)

# (tab in the address, Marketplace status, label)
TABS = [
    ("pending", "PENDING", "Pending"),
    ("approved", "APPROVED", "Approved"),
    ("rejected", "REJECTED", "Rejected"),
]


def status_label(approval_status):
    return STATUS_EXPLANATIONS.get(approval_status, (approval_status, ""))[0]


def call(request, function, *args):
    """(status, data), or (None, None) when Marketplace can't be reached."""
    try:
        return authenticated_call(request, function, *args)
    except ServiceUnavailable:
        return None, None


@admin_required
def console_home(request):
    status, summary = call(request, services.admin_summary)
    return render(request, "frontend/console_home.html", {
        "summary": summary if status == 200 else None,
        "unavailable": status != 200,
        "console_page": "home",
    })


@admin_required
def console_restaurants(request):
    tab = request.GET.get("tab", "pending")
    if tab not in {key for key, _, _ in TABS}:
        tab = "pending"
    approval_status = next(value for key, value, _ in TABS if key == tab)

    status, shops = call(request, services.admin_shops, approval_status)
    if status == 200:
        shops = [{**shop, "applied_at": lebanon_time(shop.get("created_at"))} for shop in shops]

    return render(request, "frontend/console_restaurants.html", {
        "tabs": TABS,
        "tab": tab,
        "shops": shops if status == 200 else [],
        "unavailable": status != 200,
        "console_page": "restaurants",
    })


@admin_required
def console_restaurant(request, shop_id):
    form = ShopDecisionForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        try:
            status, body = authenticated_call(
                request, services.admin_review_shop, shop_id, form.decision_payload()
            )
        except ServiceUnavailable:
            form.add_error(None, DECISION_UNCONFIRMED)
        else:
            if status == 200:
                if body["approval_status"] == "APPROVED":
                    messages.success(request, f"{body['name']} is approved and can now publish food.")
                else:
                    messages.success(request, f"{body['name']} was rejected. The owner will see your reason.")
                return redirect("console-restaurant", shop_id=shop_id)
            if status == 404:
                raise Http404("Restaurant not found.")
            form.add_marketplace_errors(body if status == 400 else None)

    status, shop = call(request, services.admin_shop, shop_id)
    if status == 404:
        raise Http404("Restaurant not found.")
    if status != 200:
        shop = None
    else:
        shop = {
            **shop,
            "status_label": status_label(shop["approval_status"]),
            "applied_at": lebanon_time(shop.get("created_at")),
            "reviewed_at": lebanon_time(shop.get("reviewed_at")),
        }

    return render(request, "frontend/console_restaurant.html", {
        "shop": shop,
        "form": form,
        "unavailable": shop is None,
        "console_page": "restaurants",
    })
