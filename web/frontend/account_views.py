"""Login, sign-up, sign-out and the shop workspace (LP-7, LP-56).

One login page serves every account; the account's role decides where it lands
and which pages it may open.
"""

from functools import wraps
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_POST

from . import services, tokens
from .browse_views import lebanon_time, pickup_window
from .listing_photos import photo_url
from .forms import LoginForm, ShopProfileForm, SignUpForm
from .services import ServiceUnavailable

ACCOUNT_KEY = "account"
PENDING_SHOP_KEY = "pending_shop_name"

CUSTOMER = "CUSTOMER"
SHOP_OPERATOR = "SHOP_OPERATOR"
ADMIN = "ADMIN"

INVALID_LOGIN = "Email or password is incorrect."
TOO_MANY_SIGN_INS = "Too many sign-in attempts. Please wait a minute and try again."
TOO_MANY_REGISTRATIONS = "Too many registration attempts from this connection. Please try again later."
SIGN_IN_UNAVAILABLE = "Sign-in is temporarily unavailable. Please try again in a few minutes."
REGISTRATION_UNCONFIRMED = (
    "We couldn't reach the account service, so we can't tell whether your account was created. "
    "Wait a few minutes, then try logging in with these details. If that fails, sign up again."
)
REGISTRATION_FAILED = "We couldn't complete your sign-up. Please check your details and try again."
SHOPS_ONLY = "That page is for restaurant accounts."
CUSTOMERS_ONLY = "That page is for customer accounts."
ADMINS_ONLY = "That page is for the LastPlate administrator."

STATUS_EXPLANATIONS = {
    "PENDING": ("Pending review", "A LastPlate administrator checks new shops before they can publish offers. You can prepare drafts now, but you can't publish yet."),
    "APPROVED": ("Approved", "Your shop is approved. You can publish food offers below."),
    "REJECTED": ("Not approved", "Your shop was not approved, so it can't publish offers and any published offers are hidden from customers. Please contact the LastPlate team."),
}


LISTING_STATUS_LABELS = {
    "DRAFT": "Draft",
    "PUBLISHED": "Published",
    "WITHDRAWN": "Withdrawn",
}


def listing_summary(listing, shop):
    """What the workspace says about an offer, including whether customers see it."""
    start = lebanon_time(listing.get("pickup_start"))
    end = lebanon_time(listing.get("pickup_end"))
    published = listing.get("status") == "PUBLISHED"
    pickup_ended = end is not None and end <= timezone.now()
    free = listing.get("free_quantity")

    hidden_because = ""
    if published:
        if shop.get("approval_status") != "APPROVED":
            hidden_because = "your shop isn't approved."
        elif pickup_ended:
            hidden_because = "its pickup time has ended."
        elif free is not None and free <= 0:
            hidden_because = "it has sold out. Add stock to show it again."

    return {
        "status_label": LISTING_STATUS_LABELS.get(listing.get("status"), listing.get("status")),
        "pickup_window": pickup_window(start, end) if start and end else "",
        "pickup_ended": pickup_ended,
        "hidden_because": hidden_because,
        "visible_to_customers": published and not hidden_because,
    }


def unapproved_shop_page(shop):
    """LP-37: where an owner goes instead of the workspace while their
    restaurant isn't approved: the request form, or their request's status."""
    status = shop.get("approval_status")
    if status == "APPROVED":
        return None
    if status == "REJECTED" or shop.get("application_sent"):
        return "request-status"
    return "restaurant-apply"


class SessionEnded(Exception):
    """The refresh token was rejected; the person must log in again."""


def signed_in(request):
    return bool(tokens.get_refresh_token(request) and request.session.get(ACCOUNT_KEY))


def landing_url(account):
    """Where an account goes after login, or when it opens a page that isn't for it."""
    if account.get("role") == ADMIN:
        return reverse("console")
    if account.get("is_staff"):
        return settings.STAFF_ADMIN_URL  # TODO: /console later
    if account.get("role") == SHOP_OPERATOR:
        return reverse("workspace")  # TODO: /business later
    return reverse("home")


def role_required(role, refusal):
    """Allow signed-in accounts with this role only; never cache; end stale
    sessions cleanly. Anyone else is sent to their own landing page."""

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not signed_in(request):
                return redirect(f"{reverse('login')}?{urlencode({'next': request.get_full_path()})}")
            account = request.session[ACCOUNT_KEY]
            if account.get("role") != role:
                messages.info(request, refusal)
                return redirect(landing_url(account))
            try:
                return view(request, *args, **kwargs)
            except SessionEnded:
                tokens.clear_tokens(request)
                messages.info(request, "Your session has ended. Please log in again.")
                return redirect("login")

        return never_cache(wrapper)

    return decorator


operator_required = role_required(SHOP_OPERATOR, SHOPS_ONLY)
customer_required = role_required(CUSTOMER, CUSTOMERS_ONLY)
admin_required = role_required(ADMIN, ADMINS_ONLY)


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


def start_session(request, token_data, account, remember=True):
    tokens.store_tokens(request, token_data["access"], token_data["refresh"])
    request.session[ACCOUNT_KEY] = {key: account[key] for key in ("id", "email", "display_name", "role")}
    if not remember:
        # Without "Keep me logged in" the session ends when the browser closes.
        # With it, the session lasts SESSION_COOKIE_AGE, the refresh-token lifetime.
        request.session.set_expiry(0)


@sensitive_variables("data")
def register_account(form, data):
    """Create the account. Returns (token_data, account) or None after adding form errors."""
    try:
        status, body = services.register(
            data["display_name"], data["email"], data["password"], data["account_type"]
        )
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


@sensitive_post_parameters("password")
@never_cache
def signup(request):
    if signed_in(request):
        return redirect(landing_url(request.session[ACCOUNT_KEY]))
    form = SignUpForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        result = register_account(form, form.cleaned_data)
        if result:
            token_data, account = result
            start_session(request, token_data, account)
            if account["role"] == SHOP_OPERATOR:
                messages.success(request, "Your account is ready. Now tell us about your restaurant.")
                return redirect("restaurant-apply")
            messages.success(request, f"Welcome to LastPlate, {account['display_name']}!")
            return redirect(landing_url(account))
    return render(request, "frontend/signup.html", {"form": form})


def safe_next(request):
    next_url = request.POST.get("next") or request.GET.get("next") or ""
    if url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return next_url
    return ""


@sensitive_post_parameters("password")
@never_cache
def login(request):
    next_url = safe_next(request)
    if signed_in(request):
        return redirect(next_url or landing_url(request.session[ACCOUNT_KEY]))

    form = LoginForm(request.POST or None)
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
            elif account.get("is_staff") and account.get("role") != ADMIN:
                # Staff work in the Django admin, which has its own login, so
                # no web session is kept for them. The Admin role uses /console.
                revoke(token_data["refresh"])
                return redirect(landing_url(account))
            elif account.get("role") not in (CUSTOMER, SHOP_OPERATOR, ADMIN):
                revoke(token_data["refresh"])
                form.add_error(None, SIGN_IN_UNAVAILABLE)
            else:
                start_session(request, token_data, account, remember=form.cleaned_data["remember_me"])
                return redirect(next_url or landing_url(account))
    return render(request, "frontend/login.html", {"form": form, "next": next_url})


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
    return redirect("login")


@customer_required
def my_account(request):
    """A customer's own account page (LP-56). Only customers may open it."""
    return render(request, "frontend/my_account.html", {"account": request.session[ACCOUNT_KEY]})


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
        return redirect("restaurant-apply")

    if status != 200:
        return render(
            request,
            "frontend/workspace.html",
            {"unavailable": True},
        )

    # LP-37: until approved, the owner sees their request instead.
    gate = unapproved_shop_page(shop)
    if gate:
        return redirect(gate)

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
                **listing_summary(listing, shop),
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
        return redirect("restaurant-apply")

    if request.method == "POST":
        form = ShopProfileForm(request.POST, current_area=(shop or {}).get("area"))
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
        form = ShopProfileForm(
            initial={field: shop.get(field, "") for field in ShopProfileForm.base_fields},
            current_area=shop.get("area"),
        )
    else:
        form = None  # cannot show current values without the shop service

    shop = shop if status == 200 else None
    status_label = STATUS_EXPLANATIONS.get(shop["approval_status"], (shop["approval_status"], ""))[0] if shop else ""
    return render(request, "frontend/shop_profile.html", {"form": form, "shop": shop, "status_label": status_label})


@operator_required
def finish_setup(request):
    """LP-37: the LP-7 one-field setup is replaced by the three-step request."""
    return redirect("restaurant-apply")
