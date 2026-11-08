"""LP-45: a rejected restaurant asks for review again.

Marketplace sets it back to Pending (never Approved) and keeps the admin's
earlier reason; the owner's optional note tells the team what changed.
"""

from django.contrib import messages
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods

from . import services
from .account_views import authenticated_call, operator_required
from .services import ServiceUnavailable

NOTE_LIMIT = 1000


@operator_required
@require_http_methods(["POST"])
def review_again(request):
    note = request.POST.get("note", "").strip()
    if len(note) > NOTE_LIMIT:
        messages.error(request, f"Keep your note to {NOTE_LIMIT} characters or fewer.")
        return redirect("request-status")
    try:
        status, body = authenticated_call(request, services.review_again, note)
    except ServiceUnavailable:
        messages.warning(
            request,
            "We couldn't confirm whether your request was sent again. Refresh this page before trying again.",
        )
        return redirect("request-status")
    if status == 200:
        messages.success(request, "Your request was sent again. The LastPlate team will review it.")
    elif status == 409:
        messages.info(request, "Your restaurant isn't waiting for this: it is already being reviewed or approved.")
    else:
        messages.error(request, "Your request could not be sent again. Please try again.")
    return redirect("request-status")
