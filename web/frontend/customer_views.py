"""LP-18: customer pages that are placeholders until their own cards are built."""

from django.shortcuts import render

from .account_views import customer_required


@customer_required
def search(request):
    return render(request, "frontend/coming_soon.html", {
        "heading": "Search",
        "message": "Searching for food is coming soon. For now you can browse what's available.",
    })


@customer_required
def my_orders(request):
    return render(request, "frontend/coming_soon.html", {
        "heading": "My orders",
        "message": "Your orders will appear here once ordering opens.",
    })