"""LP-80: /console/customers, where the admin sees expired-order strikes.

Only accounts with the Admin role may open these pages. Reservations checks
the Admin token again on every call, so this page is never the only check.
"""

from django import forms
from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render

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
    return {**customer, "blocked_until": lebanon_time(customer.get("blocked_until"))}


@admin_required
def console_customers(request):
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
