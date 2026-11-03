
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from accounts.models import Shop
from .forms import ListingForm
from .models import Listing
from .permissions import (
    require_shop_operator,
    require_approved_shop,
)


@login_required
def create_listing(request):
    require_shop_operator(request.user)

    shop = Shop.objects.filter(
        operator=request.user
    ).first()

    if shop is None:
        raise PermissionDenied(
            "Create your shop profile first."
        )

    if request.method == "POST":
        action = request.POST.get("action")

        if action not in ("draft", "publish"):
            raise PermissionDenied("Invalid listing action.")
    else:
        action = None

    publishing = action == "publish"

    if publishing:
        require_approved_shop(shop, request.user)

    if request.method == "POST":
        form = ListingForm(
            request.POST,
            publishing=publishing,
        )

        if form.is_valid():
            listing = form.save(commit=False)
            listing.shop = shop

            if not listing.pickup_address:
                listing.pickup_address = shop.address

            if publishing:
                listing.status = Listing.Status.PUBLISHED
            else:
                listing.status = Listing.Status.DRAFT

            with transaction.atomic():
                listing.save()

            return redirect("marketplace:listing_success")
    else:
        form = ListingForm(
            initial={"pickup_address": shop.address}
        )

    return render(
        request,
        "marketplace/listing_form.html",
        {"form": form, "shop": shop},
    )


@login_required
def edit_listing(request, pk):
    require_shop_operator(request.user)

    listing = get_object_or_404(
        Listing,
        pk=pk,
        shop__operator=request.user,
    )

    if request.method == "POST":
        action = request.POST.get("action")

        if action not in ("draft", "publish"):
            raise PermissionDenied("Invalid listing action.")

        if (
            listing.status == Listing.Status.PUBLISHED
            and action == "draft"
        ):
            raise PermissionDenied(
                "A published offer cannot be changed back to a draft."
            )
    else:
        action = None

    publishing = action == "publish"

    if publishing:
        require_approved_shop(
            listing.shop,
            request.user,
        )

    if request.method == "POST":
        form = ListingForm(
            request.POST,
            instance=listing,
            publishing=publishing,
        )

        if form.is_valid():
            updated = form.save(commit=False)

            if publishing:
                updated.status = Listing.Status.PUBLISHED
            else:
                updated.status = Listing.Status.DRAFT

            with transaction.atomic():
                updated.save()

            return redirect("marketplace:listing_success")
    else:
        form = ListingForm(instance=listing)

    return render(
        request,
        "marketplace/listing_form.html",
        {
            "form": form,
            "shop": listing.shop,
            "listing": listing,
        },
    )


@login_required
def listing_success(request):
    require_shop_operator(request.user)

    return render(
        request,
        "marketplace/listing_success.html",
    )
