
"""Shop-operator food listing pages (LP-10 and LP-11)."""

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from . import services
from .account_views import authenticated_call, operator_required
from .apply_views import approved_shop_required
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

# LP-38: A LastBag (surprise bag) has no title, description, category or exact
# ingredients. Its worth is original_price and its price discounted_price.
LASTBAG = "LASTBAG"
ITEM = "ITEM"

LASTBAG_FIELDS = (
    "typical_contents",
    "original_price",
    "discounted_price",
    "allergens",
    "total_quantity",
    "pickup_address",
    "pickup_start",
    "pickup_end",
)

FIELDS_BY_KIND = {
    LASTBAG: LASTBAG_FIELDS,
    ITEM: LISTING_FIELDS,
}

OPTIONAL_NUMBER_FIELDS = (
    "original_price",
    "discounted_price",
    "total_quantity",
)

MAX_PHOTO_SIZE = 5 * 1024 * 1024

FIELD_LABELS = {
    "title": "Food title",
    "description": "Description",
    "category": "Category",
    "original_price": "Original price",
    "discounted_price": "Discounted price",
    "total_quantity": "Available quantity",
    "pickup_address": "Pickup address",
    "pickup_start": "Pickup starts",
    "pickup_end": "Pickup ends",
    "ingredients": "Ingredients",
    "allergens": "Allergens",
    "typical_contents": "What's usually inside",
}

LASTBAG_LABELS = {
    **FIELD_LABELS,
    "typical_contents": "What's usually inside",
    "original_price": "Worth",
    "discounted_price": "Your price",
    "allergens": "May contain (allergens)",
    "kind": "Offer type",
}


def offer_kind(value):
    """LASTBAG or ITEM; anything else is treated as a specific item."""
    return LASTBAG if str(value or "").upper() == LASTBAG else ITEM


def listing_data_from(post, kind):
    """The fields this kind of offer sends to Marketplace.

    An item sends exactly what it did in LP-10. Empty numbers and times are
    left out, so a draft can be unfinished.
    """
    data = {field: post.get(field, "").strip() for field in FIELDS_BY_KIND[kind]}

    for field in (*OPTIONAL_NUMBER_FIELDS, "pickup_start", "pickup_end"):
        if field in data and not data[field]:
            del data[field]

    return data


def labelled_errors(errors, kind=ITEM):
    """Marketplace field errors, keyed by the labels the form shows."""
    if not isinstance(errors, dict):
        return {}

    labels = LASTBAG_LABELS if kind == LASTBAG else FIELD_LABELS

    return {
        labels.get(field, field.replace("_", " ").capitalize()): (
            problems if isinstance(problems, list) else [str(problems)]
        )
        for field, problems in errors.items()
    }


def photo_refused(response):
    """Why Marketplace refused a photo, for the message shown to the operator."""
    problems = response.get("photo") if isinstance(response, dict) else None

    if isinstance(problems, list) and problems:
        return " ".join(str(problem) for problem in problems)

    return "Please try a different JPEG or PNG."


def not_found(request, message):
    """A readable page when a listing can't be opened; never says whose it is."""
    return render(
        request,
        "frontend/not_found.html",
        {"message": message},
        status=404,
    )


def shop_pickup_address(request):
    """The shop's saved address, offered as a new offer's pickup address."""
    shop = getattr(request, "shop", None)
    if isinstance(shop, dict):
        return shop.get("address") or ""

    try:
        status, shop = authenticated_call(request, services.my_shop)
    except ServiceUnavailable:
        return ""

    if status != 200 or not isinstance(shop, dict):
        return ""

    return shop.get("address") or ""


@operator_required
@require_http_methods(["GET", "POST"])
@approved_shop_required
def create_listing(request):
    """Create a draft listing, then optionally upload its photo."""

    if request.method == "GET":
        return render(
            request,
            "frontend/create_listing.html",
            {
                "kind": offer_kind(request.GET.get("kind")),
                "form_values": {"pickup_address": shop_pickup_address(request)},
            },
        )

    # LP-38: The form says whether this is a LastBag or a specific item.
    kind = offer_kind(request.POST.get("kind"))
    listing_data = listing_data_from(request.POST, kind)
    form_values = dict(listing_data)

    if kind == LASTBAG:
        listing_data["kind"] = LASTBAG

    def form_page(status, **context):
        return render(
            request,
            "frontend/create_listing.html",
            {"kind": kind, "form_values": form_values, **context},
            status=status,
        )

    photo = request.FILES.get("photo")

    # Check the photo before creating the listing.
    if photo:
        if photo.size > MAX_PHOTO_SIZE:
            return form_page(400, error="The photo must be 5 MB or smaller.")

        if photo.content_type not in ("image/jpeg", "image/png"):
            return form_page(400, error="Upload a JPEG or PNG photo.")

    try:
        status, response = authenticated_call(
            request,
            services.create_listing,
            listing_data,
        )
    except ServiceUnavailable:
        return form_page(
            503,
            error=(
                "We couldn't confirm whether your draft was saved. "
                "Please check your workspace before trying again."
            ),
        )

    if status == 400:
        return form_page(
            400,
            error="Please correct the listing details and try again.",
            field_errors=labelled_errors(response, kind),
        )

    if status != 201:
        return form_page(
            status if 400 <= status < 600 else 502,
            error="We couldn't save your food offer. Please try again.",
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
                "Your draft was saved, but the photo was not uploaded: "
                f"{photo_refused(photo_response)} "
                "Use Edit draft to try again.",
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
@approved_shop_required
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
        return not_found(request, "We couldn't find this offer in your shop.")

    if listing.get("status") != "DRAFT":
        return not_found(
            request,
            "Only drafts can be edited. This offer has already been published.",
        )

    # LP-38: Editing a draft keeps its kind; the form never sends it.
    kind = offer_kind(listing.get("kind"))

    context = {
        "editing": True,
        "kind": kind,
        "listing": {
            **listing,
            "photo_display": photo_url(listing.get("photo_display")),
        },
        "listing_id": listing_id,
        "form_values": listing,
    }

    if request.method == "GET":
        return render(request, template, context)

    listing_data = listing_data_from(request.POST, kind)

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
        context["field_errors"] = labelled_errors(response, kind)
        return render(request, template, context, status=400)

    if update_status == 404:
        return not_found(request, "We couldn't find this draft in your shop.")

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
            photo_status, photo_response = authenticated_call(
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
                "Your changes were saved, but the photo was not updated: "
                f"{photo_refused(photo_response)}",
            )
            return redirect("workspace")

    messages.success(request, "Your draft listing was updated.")
    return redirect("workspace")

@operator_required
@require_http_methods(["POST"])
@approved_shop_required
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
        problems = labelled_errors(
            response.get("fields") if isinstance(response, dict) else None
        )
        details = "; ".join(
            f"{label}: {' '.join(errors).rstrip('.')}"
            for label, errors in problems.items()
        )
        messages.error(
            request,
            f"This offer isn't ready to publish. {details}"
            if details
            else "This offer isn't ready to publish. "
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
@approved_shop_required
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
