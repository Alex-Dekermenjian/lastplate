"""LP-50: after logging in with a temporary password, the person can only
choose a new password (or sign out). Every other page sends them there."""

from django.shortcuts import redirect

from .account_views import NEW_PASSWORD_KEY

ALLOWED_PAGES = {"choose-new-password", "sign-out", "forgot-password", "health-live", "health-ready"}


class NewPasswordRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if not request.session.get(NEW_PASSWORD_KEY):
            return None
        match = request.resolver_match
        if match and match.url_name in ALLOWED_PAGES:
            return None
        return redirect("choose-new-password")
