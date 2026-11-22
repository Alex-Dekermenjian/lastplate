from django.urls import path
from rest_framework_simplejwt.views import TokenBlacklistView, TokenRefreshView

from . import admin_views, views

urlpatterns = [
    path("auth/register/", views.ShopOperatorRegisterView.as_view(), name="auth-register"),
    path("auth/register/customer/", views.CustomerRegisterView.as_view(), name="auth-register-customer"),
    path("auth/token/", views.SignInView.as_view(), name="auth-token"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="auth-token-refresh"),
    # Sign-out: blacklists the refresh token so it cannot obtain new access
    # tokens. Access tokens already issued stay valid until they expire.
    path("auth/logout/", TokenBlacklistView.as_view(), name="auth-logout"),
    path("users/me/", views.MeView.as_view(), name="users-me"),
    # LP-50: choose a new password (required after a temporary one).
    path("users/me/password/", views.ChangePasswordView.as_view(), name="users-me-password"),
    # LP-50: /console finds an account and gives it a temporary password.
    path("admin/users/", admin_views.AdminUserSearchView.as_view(), name="admin-users"),
    path(
        "admin/users/<int:user_id>/temporary-password/",
        admin_views.AdminTemporaryPasswordView.as_view(),
        name="admin-temporary-password",
    ),
]
