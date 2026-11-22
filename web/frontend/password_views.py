"""LP-50: forgotten passwords without email.

- /login/forgot-password/ tells the person to contact LastPlate support.
- /console/accounts/ lets the administrator find an account and give it a
  temporary password, shown once on the page and never stored here.
- /account/new-password/ is the only page open after logging in with a
  temporary password (see middleware.py) until a new one is chosen.

Accounts makes the temporary password, checks the admin's token on every call
and validates the new password; these pages are never the only check.
"""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_POST

from . import services
from .account_views import (
    ACCOUNT_KEY, NEW_PASSWORD_KEY, SessionEnded, admin_required, authenticated_call, landing_url,
    session_ended, signed_in,
)
from .forms import NewPasswordForm
from .services import ServiceUnavailable

FORGOT_PASSWORD_HELP = (
    "Contact LastPlate support; after checking who you are, an admin will give you a temporary password."
)
NOT_SAVED = "We can't reach the account service right now, so your password was not changed. Please try again in a few minutes."
TOO_MANY_TRIES = "Too many attempts. Please wait a while and try again."
UNSURE = (
    "We couldn't reach the account service, so we can't tell whether a temporary password was set. "
    "If you try again, a new one replaces it."
)
NEW_PASSWORD_FIELDS = {"current_password": "current_password", "new_password": "new_password"}


def forgot_password(request):
    return render(request, "frontend/forgot_password.html", {"help_text": FORGOT_PASSWORD_HELP})


@sensitive_post_parameters("current_password", "new_password", "new_password_again")
@sensitive_variables("data")
@never_cache
def choose_new_password(request):
    if not signed_in(request):
        return redirect("login")
    account = request.session[ACCOUNT_KEY]
    if not request.session.get(NEW_PASSWORD_KEY):
        return redirect(landing_url(account))

    form = NewPasswordForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            status, body = authenticated_call(
                request, services.change_password, data["current_password"], data["new_password"]
            )
        except SessionEnded:
            return session_ended(request, account)
        except ServiceUnavailable:
            form.add_error(None, NOT_SAVED)
        else:
            if status == 200:
                del request.session[NEW_PASSWORD_KEY]
                messages.success(request, "Your new password is saved. Use it next time you log in.")
                return redirect(landing_url(account))
            if status == 400 and isinstance(body, dict):
                for field, errors in body.items():
                    form.add_error(NEW_PASSWORD_FIELDS.get(field), errors)
            elif status == 429:
                form.add_error(None, TOO_MANY_TRIES)
            else:
                form.add_error(None, NOT_SAVED)
    return render(request, "frontend/choose_new_password.html", {"form": form})


@admin_required
def console_accounts(request):
    query = (request.GET.get("q") or "").strip()[:254]
    accounts, unavailable = [], False
    if query:
        try:
            status, body = authenticated_call(request, services.admin_find_accounts, query)
        except ServiceUnavailable:
            status, body = None, None
        if status == 200 and isinstance(body, dict) and isinstance(body.get("users"), list):
            accounts = [account for account in body["users"] if isinstance(account, dict)]
        else:
            unavailable = True
    return render(request, "frontend/console_accounts.html", {
        "query": query,
        "accounts": accounts,
        "unavailable": unavailable,
        "console_page": "accounts",
    })


@admin_required
@require_POST
@sensitive_variables("body", "temporary_password")
def console_temporary_password(request, user_id):
    """Shown once, in this response only: never put in a message, the
    session or a log, and never cached by the browser (admin_required)."""
    try:
        status, body = authenticated_call(request, services.admin_set_temporary_password, user_id)
    except ServiceUnavailable:
        messages.error(request, UNSURE)
        return redirect("console-accounts")
    if status == 404:
        raise Http404("Account not found.")
    temporary_password = body.get("temporary_password") if isinstance(body, dict) else None
    if status != 200 or not temporary_password:
        messages.error(request, (body or {}).get("detail") or "That didn't work. Please try again.")
        return redirect("console-accounts")
    return render(request, "frontend/console_temporary_password.html", {
        "person": body.get("user") or {},
        "temporary_password": temporary_password,
        "console_page": "accounts",
    })
