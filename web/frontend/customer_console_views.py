"""LP-80: /console/customers, where the admin sees expired-order strikes.
LP-60: and blocks or unblocks a customer, suspends or restores a
restaurant, and removes a single offer.

Only accounts with the Admin role may open these pages. Reservations and
Marketplace check the Admin token again on every call, so this page is never
the only check.
"""

from django import forms
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from . import services
from .account_views import admin_required, authenticated_call
from .browse_views import lebanon_time
from .services import ServiceUnavailable

UNCONFIRMED = ("We couldn't reach the orders service, so we can't tell whether the strike was removed. "
               "Check the list below before trying again.")


class RemoveStrikeForm(forms.Form):
    reservation_ref = forms.UUIDField(widget=forms.HiddenInput)
    reason = forms.CharField(label="Why remove this strike?", max_length=1000,
                             error_messages={"required": "Say why the strike is removed."})

    def clean_reason(self):
        reason = self.cleaned_data["reason"].strip()
        if not reason:
            raise forms.ValidationError("Say why the strike is removed.")
        return reason


def _call(request, function, *args):
    try:
        return authenticated_call(request, function, *args)
    except ServiceUnavailable:
        return None, None


def _with_times(customer):
    admin_block = customer.get("admin_block") if isinstance(customer.get("admin_block"), dict) else None
    return {**customer, "blocked_until": lebanon_time(customer.get("blocked_until")),
            "ordering_blocked_until": lebanon_time(customer.get("ordering_blocked_until")),
            "admin_block": admin_block and {**admin_block, "blocked_until": lebanon_time(admin_block.get("blocked_until"))}}


@admin_required
def console_customers(request):
    # LP-60: find any customer by account number, to block them.
    account = (request.GET.get("account") or "").strip().lstrip("#")
    if account.isdigit() and int(account) > 0:
        return redirect("console-customer", customer_user_id=int(account))
    status, data = _call(request, services.admin_customers)
    ok = status == 200 and isinstance(data, dict) and isinstance(data.get("customers"), list)
    return render(request, "frontend/console_customers.html", {
        "customers": [_with_times(c) for c in data["customers"] if isinstance(c, dict)] if ok else [],
        "unavailable": not ok,
        "console_page": "customers",
    })


@admin_required
def console_customer(request, customer_user_id):
    form = RemoveStrikeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            status, body = authenticated_call(request, services.admin_remove_strike,
                                              form.cleaned_data["reservation_ref"], form.cleaned_data["reason"])
        except ServiceUnavailable:
            form.add_error(None, UNCONFIRMED)
        else:
            if status == 200:
                messages.success(request, "Strike removed. The reason is saved with the order.")
                return redirect("console-customer", customer_user_id=customer_user_id)
            form.add_error(None, (body or {}).get("detail") or "The strike could not be removed.")

    status, customer = _call(request, services.admin_customer, customer_user_id)
    if status == 404:
        raise Http404("Customer not found.")
    if status != 200 or not isinstance(customer, dict):
        customer = None
    else:
        customer = _with_times(customer)
        customer["expired_orders"] = [
            {**order, "pick_up_by": lebanon_time(order.get("pick_up_by")),
             "removed": order["removed"] and {**order["removed"], "at": lebanon_time(order["removed"].get("at"))}}
            for order in customer.get("expired_orders") or [] if isinstance(order, dict)
        ]
    return render(request, "frontend/console_customer.html", {
        "customer": customer,
        "customer_user_id": customer_user_id,
        "form": form,
        "unavailable": customer is None,
        "console_page": "customers",
    })


# ---------------------------------------------------------------------------
# LP-60: moderation actions. Each posts once, says what happened, and goes
# back to the page it came from.
# ---------------------------------------------------------------------------

UNSURE = "We couldn't reach the service, so we can't tell whether this was saved. Check the page before trying again."


def _act(request, function, *args, done):
    """Run one admin call; flash the outcome. Returns the HTTP status or None."""
    try:
        status, body = authenticated_call(request, function, *args)
    except ServiceUnavailable:
        messages.error(request, UNSURE)
        return None
    if status == 200:
        messages.success(request, done)
    elif status == 404:
        raise Http404("Not found.")
    else:
        messages.error(request, (body or {}).get("detail") or "That didn't work. Please try again.")
    return status


def _reason(request):
    return (request.POST.get("reason") or "").strip()


@admin_required
@require_POST
def suspend_restaurant(request, shop_id):
    reason = _reason(request)
    if not reason:
        messages.error(request, "Give a reason. The owner will see it.")
    else:
        _act(request, services.admin_suspend_shop, shop_id, reason,
             done="Restaurant suspended. Its offers are hidden and the owner sees your reason.")
    return redirect("console-restaurant", shop_id=shop_id)


@admin_required
@require_POST
def restore_restaurant(request, shop_id):
    _act(request, services.admin_restore_shop, shop_id, done="Restaurant restored. Its offers are back.")
    return redirect("console-restaurant", shop_id=shop_id)


@admin_required
@require_POST
def remove_offer(request, shop_id, listing_id):
    reason = _reason(request)
    if not reason:
        messages.error(request, "Give a reason. The owner will see it.")
    else:
        _act(request, services.admin_remove_listing, listing_id, reason,
             done="Offer removed. Existing orders for it stay valid.")
    return redirect("console-restaurant", shop_id=shop_id)


@admin_required
@require_POST
def block_customer(request, customer_user_id):
    reason, until = _reason(request), (request.POST.get("until") or "").strip()
    if not reason or not until:
        messages.error(request, "Choose a day and give a reason.")
    else:
        _act(request, services.admin_block_customer, customer_user_id, until, reason,
             done="Customer blocked from ordering. They can still browse.")
    return redirect("console-customer", customer_user_id=customer_user_id)


@admin_required
@require_POST
def unblock_customer(request, customer_user_id):
    _act(request, services.admin_unblock_customer, customer_user_id, done="Customer unblocked.")
    return redirect("console-customer", customer_user_id=customer_user_id)
