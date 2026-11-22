"""LP-50: the account endpoints behind /console (Admin role or staff only).

An admin finds an account and gives it a temporary password, because
LastPlate sends no email. The password is created here, saved only as a hash,
returned once in the response and never logged. The person must replace it
at their next login.
"""

import logging
import secrets

from django.contrib.auth import get_user_model, password_validation
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_variables
from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from .models import TemporaryPasswordLog
from .serializers import AdminUserSerializer, can_get_temporary_password

User = get_user_model()
audit = logging.getLogger("accounts.audit")

SEARCH_LIMIT = 20
# No 0/O or 1/l/I, so the admin can read it out without mistakes.
ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
NOT_ALLOWED = "Temporary passwords are only for customer and restaurant accounts."


class IsTeamAdmin(BasePermission):
    """The LastPlate administrator (role ADMIN) or a staff member. The user is
    read from this service's own database, so a forged claim grants nothing."""

    message = "Only LastPlate administrators can do this."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user and user.is_authenticated and user.is_active
            and (user.role == User.Role.ADMIN or user.is_staff)
        )


ADMIN_ONLY = [IsAuthenticated, IsTeamAdmin]


@sensitive_variables("password")
def new_temporary_password(user):
    """Three groups of four, for example "Hk7m-Pq2x-Rt9b": about 69 random bits."""
    while True:
        password = "-".join("".join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(3))
        try:
            password_validation.validate_password(password, user=user)
        except ValidationError:
            continue
        return password


def revoke_sign_ins(user):
    """Every refresh token the account holds stops working, so whoever was
    logged in (perhaps not the owner) must log in with the new password."""
    open_tokens = OutstandingToken.objects.filter(user=user, blacklistedtoken__isnull=True)
    BlacklistedToken.objects.bulk_create(
        [BlacklistedToken(token=token) for token in open_tokens], ignore_conflicts=True
    )


class AdminUserSearchView(APIView):
    """GET /api/admin/users/?q=  by account number or part of an email."""

    permission_classes = ADMIN_ONLY

    def get(self, request):
        query = (request.query_params.get("q") or "").strip().lstrip("#")
        if not query:
            return Response({"users": []})
        matches = Q(email__icontains=query)
        if query.isdigit():
            matches |= Q(pk=int(query))
        users = User.objects.filter(matches).order_by("email")[:SEARCH_LIMIT]
        return Response({"users": AdminUserSerializer(users, many=True).data})


@method_decorator(sensitive_variables("password"), name="post")
class AdminTemporaryPasswordView(APIView):
    """POST /api/admin/users/{id}/temporary-password/"""

    permission_classes = ADMIN_ONLY

    def post(self, request, user_id):
        with transaction.atomic():
            user = get_object_or_404(User.objects.select_for_update(), pk=user_id)
            if user.pk == request.user.pk or not can_get_temporary_password(user):
                return Response({"code": "not_allowed", "detail": NOT_ALLOWED}, status=status.HTTP_403_FORBIDDEN)
            password = new_temporary_password(user)
            user.set_password(password)
            user.must_change_password = True
            user.save(update_fields=["password", "must_change_password"])
            revoke_sign_ins(user)
            entry = TemporaryPasswordLog.objects.create(
                user=user, user_email=user.email, set_by=request.user, set_by_email=request.user.email,
            )

        audit.info(
            "Temporary password set for account %s by account %s at %s.",
            user.pk, request.user.pk, entry.created_at.isoformat(),
        )
        response = Response({
            "user": AdminUserSerializer(user).data,
            "temporary_password": password,
            "set_at": entry.created_at,
        })
        response["Cache-Control"] = "no-store"
        return response
