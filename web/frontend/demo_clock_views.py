"""Demo clock: set the date and time the whole app uses (development only)."""

from datetime import datetime, timedelta

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from config import demo_clock

from . import services

FORMATS = ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M")


def parse_moment(text):
    """The date and time typed in the box (Lebanon time), or None."""
    for fmt in FORMATS:
        try:
            naive = datetime.strptime((text or "").strip(), fmt)
        except ValueError:
            continue
        return timezone.make_aware(naive)
    return None


def back_to(request):
    target = request.POST.get("next", "")
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return reverse("home")


def delete_future_orders(request, seconds):
    """The clock moved back: orders placed after the new time are deleted for
    good (and their food goes back on sale), so none is an order from the future."""
    since = demo_clock.real_now() + timedelta(seconds=seconds)
    try:
        status, body = services.rewind_orders(since)
    except services.ServiceUnavailable:
        status, body = None, {}
    if status != 200:
        messages.warning(request, "The clock is set, but orders placed after this time could not be deleted. "
                                  "Set the time again to retry.")
        return
    removed = body.get("orders_removed", 0)
    if removed:
        messages.info(request, f"{removed} order{'s' if removed != 1 else ''} placed after this time "
                               f"{'were' if removed != 1 else 'was'} deleted and the food is back on sale.")


@require_POST
def set_clock(request):
    if not demo_clock.enabled():
        raise Http404
    if request.POST.get("action") == "reset":
        seconds = 0
        done = "The clock now shows the real time."
    else:
        moment = parse_moment(request.POST.get("moment"))
        if moment is None:
            messages.error(request, "Type a date and time, for example 2026-11-20 18:30:00.")
            return redirect(back_to(request))
        seconds = round((moment - demo_clock.real_now()).total_seconds())
        if abs(seconds) > demo_clock.MAX_OFFSET_SECONDS:
            messages.error(request, "That date is too far from today. Pick one within ten years.")
            return redirect(back_to(request))
        done = "The clock is set. Every part of the app now uses this time."
    before = demo_clock.offset_seconds()
    try:
        demo_clock.set_offset(seconds)
    except OSError:
        messages.error(request, "Couldn't save the clock. Please try again.")
    else:
        messages.success(request, done)
        if seconds <= before:  # not moved forward: nothing may be dated after "now"
            delete_future_orders(request, seconds)
    return redirect(back_to(request))
