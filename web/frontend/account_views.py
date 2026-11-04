"""Shop-operator registration, sign-in, sign-out and workspace (LP-7)."""

from functools import wraps
from urllib.parse import urlencode

from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_POST

from . import services, tokens
from .listing_photos import photo_url
from .forms import RegistrationForm, ShopProfileForm, ShopSetupForm, SignInForm
from .services import ServiceUnavailable

ACCOUNT_KEY = "account"
PENDING_SHOP_KEY = "pending_shop_name"

INVALID_LOGIN = "Email or password is incorrect."
TOO_MANY_SIGN_INS = "Too many sign-in attempts. Please wait a minute and try again."
TOO_MANY_REGISTRATIONS = "Too many registration attempts from this connection. Please try again later."
SIGN_IN_UNAVAILABLE = "Sign-in is temporarily unavailable. Please try again in a few minutes."
REGISTRATION_UNCONFIRMED = (
    "We couldn't reach the account service, so we can't tell whether your account was created. "
    "Wait a few minutes, then try signing in with these details. If that fails, register again."
)
REGISTRATION_FAILED = "We couldn't complete your registration. Please check your details and try again."
OPERATORS_ONLY = "This sign-in is for shop operators. Customer accounts are not available yet."

STATUS_EXPLANATIONS = {
    "PENDING": ("Pending review", "A LastPlate administrator checks new shops before they can publish offers. You can't publish yet."),
    "APPROVED": ("Approved", "Your shop is approved. Publishing offers arrives in a later release."),
    "REJECTED": ("Not approved", "Your shop was not approved. Please contact the LastPlate team."),
}


class SessionEnded(Exception):
    """The refresh token was rejected; the operator must sign in again."""


def signed_in(request):
    return bool(tokens.get_refresh_token(request) and request.session.get(ACCOUNT_KEY))


def operator_required(view):
    """Allow signed-in operators only; never cache; end stale sessions cleanly."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not signed_in(request):
            return redirect(f"{reverse('sign-in')}?{urlencode({'next': request.get_full_path()})}")
        try:
            return view(request, *args, **kwargs)
        except SessionEnded:
            tokens.clear_tokens(request)
            messages.info(request, "Your session has ended. Please sign in again.")
            return redirect("sign-in")

    return never_cache(wrapper)


def authenticated_call(request, call, *args):
    """Call a service with the session's access token, renewing it once if expired."""
    status, data = call(tokens.get_access_token(request), *args)
    if status != 401:
        return status, data
    refresh_status, refreshed = services.refresh(tokens.get_refresh_token(request))
    if refresh_status != 200 or "access" not in refreshed:
        raise SessionEnded
    tokens.set_access_token(request, refreshed["access"])
    return call(refreshed["access"], *args)


def start_session(request, token_data, account):
    tokens.store_tokens(request, token_data["access"], token_data["refresh"])
    request.session[ACCOUNT_KEY] = {key: account[key] for key in ("id", "email", "display_name", "role")}


def create_pending_shop(request, name):
    """Ask Marketplace for the operator's shop. Safe to repeat: Marketplace
    returns the existing shop instead of creating a second one."""
    try:
        status, _ = authenticated_call(request, services.create_shop, name)
    except (ServiceUnavailable, SessionEnded):
        return False
    if status in (200, 201):
        request.session.pop(PENDING_SHOP_KEY, None)
        return True
    return False


@sensitive_variables("data")
def register_account(form, data):
    """Create the account. Returns (token_data, account) or None after adding form errors."""
    try:
        status, body = services.register(data["display_name"], data["email"], data["password"])
    except ServiceUnavailable:
        return recover_unconfirmed_registration(form, data)
    if status == 201:
        return body, body["user"]
    if status == 429:
        form.add_error(None, TOO_MANY_REGISTRATIONS)
    elif status == 400 and isinstance(body, dict):
        for field, errors in body.items():
            form.add_error(field if field in form.fields else None, errors)
    else:
        form.add_error(None, REGISTRATION_FAILED)
    return None


@sensitive_variables("data", "body")
def recover_unconfirmed_registration(form, data):
    """The outcome is unknown. Only a successful sign-in with the same email
    and password proves the account exists and belongs to this person."""
    try:
        status, body = services.sign_in(data["email"], data["password"])
        if status == 200:
            account_status, account = services.account(body["access"])
            if account_status == 200:
                return body, account
    except ServiceUnavailable:
        form.add_error(None, REGISTRATION_UNCONFIRMED)
        return None
    form.add_error(None, REGISTRATION_FAILED)
    return None


@sensitive_post_parameters("password", "password_confirm")
@never_cache
def register(request):
    if signed_in(request):
        return redirect("workspace")
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        result = register_account(form, form.cleaned_data)
        if result:
            start_session(request, *result)
            shop_name = form.cleaned_data["shop_name"]
            request.session[PENDING_SHOP_KEY] = shop_name
            if create_pending_shop(request, shop_name):
                return redirect("workspace")
            messages.warning(
                request,
                "Your account is ready, but we couldn't set up your shop yet. "
                "Use “Finish shop setup” to try again.",
            )
            return redirect("finish-setup")
    return render(request, "frontend/register.html", {"form": form})


@sensitive_post_parameters("password")
@never_cache
def sign_in(request):
    next_url = request.POST.get("next") or request.GET.get("next") or ""
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        next_url = ""
    if signed_in(request):
        return redirect(next_url or "workspace")

    form = SignInForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            status, token_data = services.sign_in(form.cleaned_data["email"], form.cleaned_data["password"])
            if status == 200:
                account_status, account = services.account(token_data["access"])
        except ServiceUnavailable:
            form.add_error(None, SIGN_IN_UNAVAILABLE)
        else:
            if status == 429:
                form.add_error(None, TOO_MANY_SIGN_INS)
            elif status != 200:
                form.add_error(None, INVALID_LOGIN)
            elif account_status != 200:
                form.add_error(None, SIGN_IN_UNAVAILABLE)
            elif account["role"] != "SHOP_OPERATOR":
                revoke(token_data["refresh"])
                form.add_error(None, OPERATORS_ONLY)
            else:
                start_session(request, token_data, account)
                return redirect(next_url or "workspace")
    return render(request, "frontend/sign_in.html", {"form": form, "next": next_url})


def revoke(refresh_token):
    """Best effort: True if Accounts confirmed the refresh token can no longer be used."""
    try:
        status, _ = services.sign_out(refresh_token)
    except ServiceUnavailable:
        return False
    return status in (200, 401)  # 401: already invalid, expired or blacklisted


@require_POST
@never_cache
def sign_out(request):
    refresh_token = tokens.get_refresh_token(request)
    revoked = revoke(refresh_token) if refresh_token else True
    tokens.clear_tokens(request)  # always end the local session
    if revoked:
        messages.success(request, "You have signed out.")
    else:
        messages.warning(
            request,
            "You have signed out of this browser. We couldn't reach the account service to revoke "
            "your sign-in token; it was stored only on our server, has been deleted, and expires within 24 hours.",
        )
    return redirect("sign-in")


@operator_required

def workspace(request):
    try:
        status, shop = authenticated_call(
            request,
            services.my_shop,
        )
    except ServiceUnavailable:
        status, shop = None, None

    if status == 404:
        return redirect("finish-setup")

    if status != 200:
        return render(
            request,
            "frontend/workspace.html",
            {"unavailable": True},
        )

    label, explanation = STATUS_EXPLANATIONS.get(
        shop["approval_status"],
        (shop["approval_status"], ""),
    )

    # LP-10 and LP-11: Load this operator's listings.
    try:
        listings_status, listings = authenticated_call(
            request,
            services.my_listings,
        )
    except ServiceUnavailable:
        listings_status, listings = None, None

    listings_unavailable = listings_status != 200

    if listings_unavailable:
        listings = []
    else:
    # Convert Marketplace media paths into browser-accessible URLs.
        listings = [
            {
                **listing,
                "photo_display": photo_url(listing.get("photo_display")),
            }
            for listing in listings
        ]

    return render(
        request,
        "frontend/workspace.html",
        {
            "shop": shop,
            "status_label": label,
            "status_explanation": explanation,
            "listings": listings,
            "listings_unavailable": listings_unavailable,
        },
    )



PROFILE_UNAVAILABLE = "We can't reach the shop service right now, so your changes were not saved. Please try again in a few minutes."


@operator_required
def shop_profile(request):
    """View and edit the operator's own shop profile (LP-8). Marketplace finds
    the shop from the access token, so no shop ID is ever sent from here."""
    try:
        status, shop = authenticated_call(request, services.my_shop)
    except ServiceUnavailable:
        status, shop = None, None
    if status == 404:
        return redirect("finish-setup")

    if request.method == "POST":
        form = ShopProfileForm(request.POST)
        if form.is_valid():
            try:
                save_status, body = authenticated_call(request, services.update_shop, form.cleaned_data)
            except ServiceUnavailable:
                form.add_error(None, PROFILE_UNAVAILABLE)
            else:
                if save_status == 200:
                    messages.success(request, "Your shop profile has been saved.")
                    return redirect("workspace")
                if save_status == 400 and isinstance(body, dict):
                    for field, errors in body.items():
                        form.add_error(field if field in form.fields else None, errors)
                else:
                    form.add_error(None, PROFILE_UNAVAILABLE)
    elif status == 200:
        form = ShopProfileForm(initial={field: shop.get(field, "") for field in ShopProfileForm.base_fields})
    else:
        form = None  # cannot show current values without the shop service

    shop = shop if status == 200 else None
    status_label = STATUS_EXPLANATIONS.get(shop["approval_status"], (shop["approval_status"], ""))[0] if shop else ""
    return render(request, "frontend/shop_profile.html", {"form": form, "shop": shop, "status_label": status_label})


@operator_required
def finish_setup(request):
    if request.method == "GET":
        try:
            status, _ = authenticated_call(request, services.my_shop)
        except ServiceUnavailable:
            status = None
        if status == 200:
            return redirect("workspace")
    form = ShopSetupForm(request.POST or None, initial={"shop_name": request.session.get(PENDING_SHOP_KEY, "")})
    if request.method == "POST" and form.is_valid():
        if create_pending_shop(request, form.cleaned_data["shop_name"]):
            messages.success(request, "Your shop is set up and waiting for review.")
            return redirect("workspace")
        form.add_error(None, "We still couldn't reach the shop service. Your account is safe; please try again in a few minutes.")
    return render(request, "frontend/finish_setup.html", {"form": form})
