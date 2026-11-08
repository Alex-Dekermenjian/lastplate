"""LP-58: Mark sold out and Delete draft, from the owner's offer list.

Both ask Marketplace, which decides: only the owner's own offers, only
published ones can be sold out, only drafts can be deleted.
"""

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from . import services
from .account_views import authenticated_call, operator_required
from .apply_views import approved_shop_required
from .services import ServiceUnavailable

SOLD_OUT_ERRORS = {
    "already_sold_out": "This offer is already sold out.",
    "not_published": "Only live offers can be marked sold out.",
    "pickup_ended": "This offer's pickup time has ended, so it's already off sale.",
    "below_committed": "Customers have already reserved some of this offer, so it can't be emptied.",
    "not_owner": "You can only change your own offers.",
    "no_shop": "You can only change your own offers.",
}

DELETE_ERRORS = {
    "not_a_draft": "Only drafts can be deleted. A live offer ends or sells out instead.",
    "not_found": "This draft could not be found. It may already be deleted.",
    "no_shop": "You can only delete your own drafts.",
}


def back_to(tab):
    return redirect(f"{reverse('workspace')}?tab={tab}")


@operator_required
@require_http_methods(["POST"])
@approved_shop_required
def mark_sold_out(request, listing_id):
    try:
        status, body = authenticated_call(request, services.mark_sold_out, listing_id)
    except ServiceUnavailable:
        messages.warning(
            request,
            "We couldn't confirm whether the offer was marked sold out. Check it before trying again.",
        )
        return back_to("live")
    if status == 201:
        title = ((body or {}).get("listing") or {}).get("title") or "Your offer"
        messages.success(request, f"{title} is marked sold out. Customers no longer see it.")
        return back_to("live")
    code = body.get("code") if isinstance(body, dict) else None
    messages.error(request, SOLD_OUT_ERRORS.get(code, "Marking sold out was unsuccessful. Please try again."))
    return back_to("live")


@operator_required
@require_http_methods(["POST"])
def delete_draft(request, listing_id):
    try:
        status, body = authenticated_call(request, services.delete_draft, listing_id)
    except ServiceUnavailable:
        messages.warning(
            request,
            "We couldn't confirm whether the draft was deleted. Check your drafts before trying again.",
        )
        return back_to("drafts")
    if status == 204:
        messages.success(request, "The draft was deleted.")
        return back_to("drafts")
    code = body.get("code") if isinstance(body, dict) else None
    messages.error(request, DELETE_ERRORS.get(code, "Deleting the draft was unsuccessful. Please try again."))
    return back_to("drafts")
