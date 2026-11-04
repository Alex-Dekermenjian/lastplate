
"""Shop-operator food listing pages (LP-10 and LP-11)."""

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from . import services
from .account_views import authenticated_call, operator_required
from .services import ServiceUnavailable
from .listing_photos import photo_url


LISTING_FIELDS = (
    "title",
    "description",
    "category",
    "original_price",
    "discounted_price",
    "total_quantity",
    "pickup_address",
    "pickup_start",
    "pickup_end",
    "ingredients",
    "allergens",
)

OPTIONAL_NUMBER_FIELDS = (
    "original_price",
    "discounted_price",
    "total_quantity",
)

MAX_PHOTO_SIZE = 5 * 1024 * 1024


@operator_required
@require_http_methods(["GET", "POST"])
def create_listing(request):
    """Create a draft listing, then optionally upload its photo."""

    if request.method == "GET":
        return render(request, "frontend/create_listing.html")

    listing_data = {
        field: request.POST.get(field, "").strip()
        for field in LISTING_FIELDS
    }

    for field in OPTIONAL_NUMBER_FIELDS:
        if not listing_data[field]:
            del listing_data[field]

    for field in ("pickup_start", "pickup_end"):
        if not listing_data[field]:
            del listing_data[field]

    photo = request.FILES.get("photo")

    # Check the photo before creating the listing.
    if photo:
        if photo.size > MAX_PHOTO_SIZE:
            return render(
                request,
                "frontend/create_listing.html",
                {
                    "error": "The photo must be 5 MB or smaller.",
                    "form_values": listing_data,
                },
                status=400,
            )

        if photo.content_type not in ("image/jpeg", "image/png"):
            return render(
                request,
                "frontend/create_listing.html",
                {
                    "error": "Upload a JPEG or PNG photo.",
                    "form_values": listing_data,
                },
                status=400,
            )

    try:
        status, response = authenticated_call(
            request,
            services.create_listing,
            listing_data,
        )
    except ServiceUnavailable:
        return render(
            request,
            "frontend/create_listing.html",
            {
                "error": (
                    "We couldn't confirm whether your draft was saved. "
                    "Please check your workspace before trying again."
                ),
                "form_values": listing_data,
            },
            status=503,
        )

    if status == 400:
        return render(
            request,
            "frontend/create_listing.html",
            {
                "error": "Please correct the listing details and try again.",
                "field_errors": response,
                "form_values": listing_data,
            },
            status=400,
        )

    if status != 201:
        return render(
            request,
            "frontend/create_listing.html",
            {
                "error": "We couldn't save your food offer. Please try again.",
                "form_values": listing_data,
            },
            status=status if 400 <= status < 600 else 502,
        )

    # The draft has been created successfully.
    listing_id = response.get("id")

    if photo:
        if not listing_id:
            messages.warning(
                request,
                "Your draft was saved, but the photo could not be "
                "attached because the listing ID was missing.",
            )
            return redirect("workspace")

        try:
            photo_status, photo_response = authenticated_call(
                request,
                services.upload_listing_photo,
                listing_id,
                photo,
            )
        except ServiceUnavailable:
            messages.warning(
                request,
                "Your draft was saved, but we couldn't confirm "
                "whether the photo uploaded. Check your workspace "
                "before uploading it again.",
            )
            return redirect("workspace")

        if photo_status != 200:
            messages.warning(
                request,
                "Your draft was saved, but the photo was not uploaded. "
                "You can add it later.",
            )
            return redirect("workspace")

        messages.success(
            request,
            "Your food offer and photo were saved as a draft.",
        )
        return redirect("workspace")

    messages.success(
        request,
        "Your food offer was saved as a draft.",
    )
    return redirect("workspace")

@operator_required
@require_http_methods(["GET", "POST"])
def edit_listing(request, listing_id):
    """Edit an existing draft owned by the signed-in shop."""

    template = "frontend/create_listing.html"

    # The Marketplace API returns only this operator's listings.
    try:
        status, data = authenticated_call(
            request,
            services.my_listings,
        )
    except ServiceUnavailable:
        return render(
            request,
            template,
            {"error": "We couldn't load your listing. Please try again."},
            status=503,
        )

    if status != 200 or not isinstance(data, list):
        return render(
            request,
            template,
            {"error": "We couldn't load your listings."},
            status=502,
        )

    listing = next(
        (
            item for item in data
            if str(item.get("id")) == str(listing_id)
        ),
        None,
    )

    if listing is None:
        from django.http import Http404
        raise Http404("Listing not found.")

    if listing.get("status") != "DRAFT":
        from django.http import Http404
        raise Http404("Only draft listings can be edited.")

    context = {
        "editing": True,
        "listing": {
            **listing,
            "photo_display": photo_url(listing.get("photo_display")),
        },
        "listing_id": listing_id,
        "form_values": listing,
    }

    if request.method == "GET":
        return render(request, template, context)

    listing_data = {
        field: request.POST.get(field, "").strip()
        for field in LISTING_FIELDS
    }

    for field in OPTIONAL_NUMBER_FIELDS:
        if not listing_data[field]:
            del listing_data[field]

    for field in ("pickup_start", "pickup_end"):
        if not listing_data[field]:
            del listing_data[field]

    context["form_values"] = {
        **listing,
        **listing_data,
    }

    photo = request.FILES.get("photo")

    if photo:
        if photo.size > MAX_PHOTO_SIZE:
            context["error"] = "The photo must be 5 MB or smaller."
            return render(request, template, context, status=400)

        if photo.content_type not in ("image/jpeg", "image/png"):
            context["error"] = "Upload a JPEG or PNG photo."
            return render(request, template, context, status=400)

    try:
        update_status, response = authenticated_call(
            request,
            services.edit_listing,
            listing_id,
            listing_data,
        )
    except ServiceUnavailable:
        context["error"] = (
            "We couldn't confirm whether your changes were saved. "
            "Check your workspace before trying again."
        )
        return render(request, template, context, status=503)

    if update_status == 400:
        context["error"] = "Please correct the listing details."
        context["field_errors"] = response
        return render(request, template, context, status=400)

    if update_status == 404:
        from django.http import Http404
        raise Http404("Draft listing not found.")

    if update_status != 200:
        context["error"] = "We couldn't update your listing."
        return render(
            request,
            template,
            context,
            status=update_status if 400 <= update_status < 600 else 502,
        )

    # Save the replacement photo separately.
    if photo:
        try:
            photo_status, _ = authenticated_call(
                request,
                services.upload_listing_photo,
                listing_id,
                photo,
            )
        except ServiceUnavailable:
            messages.warning(
                request,
                "Your changes were saved, but we couldn't confirm "
                "whether the photo uploaded.",
            )
            return redirect("workspace")

        if photo_status != 200:
            messages.warning(
                request,
                "Your changes were saved, but the photo was not updated.",
            )
            return redirect("workspace")

    messages.success(request, "Your draft listing was updated.")
    return redirect("workspace")

@operator_required
@require_http_methods(["POST"])
def publish_listing(request, listing_id):
    """Publish a draft through the Marketplace API."""

    try:
        status, response = authenticated_call(
            request,
            services.publish_listing,
            listing_id,
        )
    except ServiceUnavailable:
        messages.warning(
            request,
            "We couldn't confirm whether the offer was published. "
            "Check its status before trying again.",
        )
        return redirect("workspace")

    if status == 200:
        messages.success(
            request,
            "Your food offer has been published successfully!",
        )
    elif status == 400:
        messages.error(
            request,
            "This listing isn't ready to publish. "
            "Please check its required details and pickup window.",
        )
    elif status == 403:
        messages.error(
            request,
            "Your shop must be approved and have a complete profile "
            "before publishing.",
        )
    elif status == 404:
        messages.error(
            request,
            "The draft listing could not be found.",
        )
    else:
        messages.error(
            request,
            "Publishing was unsuccessful. Please try again.",
        )

    return redirect("workspace")


# LP-12: Messages for refusals from the Marketplace stock API.
STOCK_ERROR_MESSAGES = {
    "zero_change": "Enter a quantity change other than zero.",
    "reason_required": "Give a reason for the stock change.",
    "invalid": (
        "Enter a whole number to add or remove, and a reason "
        "of up to 500 characters."
    ),
    "below_zero": "You can't remove more units than the offer has.",
    "below_committed": (
        "You can't remove units that customers have already reserved "
        "or collected."
    ),
    "not_published": "Only published offers can have their stock adjusted.",
    "pickup_ended": (
        "The pickup window has ended, so this offer's stock "
        "can no longer change."
    ),
    "not_owner": "You can only adjust stock for your own offers.",
    "no_shop": "You can only adjust stock for your own offers.",
    "too_large": "That quantity is too large.",
}


@operator_required
@require_http_methods(["POST"])
def adjust_stock(request, listing_id):
    """Add or remove units of a published offer, with a reason."""

    raw_change = request.POST.get("quantity_change", "").strip()
    reason = request.POST.get("reason", "").strip()

    try:
        quantity_change = int(raw_change)
    except ValueError:
        quantity_change = None

    if quantity_change is None:
        messages.error(
            request,
            "Enter a whole number to add (for example 3) "
            "or remove (for example -2).",
        )
        return redirect("workspace")

    if quantity_change == 0:
        messages.error(request, STOCK_ERROR_MESSAGES["zero_change"])
        return redirect("workspace")

    if not reason:
        messages.error(request, STOCK_ERROR_MESSAGES["reason_required"])
        return redirect("workspace")

    try:
        status, response = authenticated_call(
            request,
            services.adjust_stock,
            listing_id,
            quantity_change,
            reason,
        )
    except ServiceUnavailable:
        # The change may or may not have been saved; retrying blindly
        # could apply it twice.
        messages.warning(
            request,
            "We couldn't confirm whether the stock was changed. "
            "Check the offer's available quantity before trying again.",
        )
        return redirect("workspace")

    if status == 201:
        listing = response.get("listing") or {}
        free = listing.get("free_quantity")
        if quantity_change > 0:
            done = f"Added {quantity_change} unit{'s' if quantity_change != 1 else ''}."
        else:
            removed = -quantity_change
            done = f"Removed {removed} unit{'s' if removed != 1 else ''}."
        if free is not None:
            done += f" {free} now available."
        messages.success(request, done)
        return redirect("workspace")

    code = response.get("code") if isinstance(response, dict) else None
    messages.error(
        request,
        STOCK_ERROR_MESSAGES.get(
            code,
            "The stock change was unsuccessful. Please try again.",
        ),
    )
    return redirect("workspace")
