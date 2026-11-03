from django.urls import path
from rest_framework_simplejwt.views import TokenBlacklistView, TokenRefreshView

from . import views

urlpatterns = [
    path("auth/register/", views.ShopOperatorRegisterView.as_view(), name="auth-register"),
    path("auth/token/", views.SignInView.as_view(), name="auth-token"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="auth-token-refresh"),
    # Sign-out: blacklists the refresh token so it cannot obtain new access
    # tokens. Access tokens already issued stay valid until they expire.
    path("auth/logout/", TokenBlacklistView.as_view(), name="auth-logout"),
    path("users/me/", views.MeView.as_view(), name="users-me"),
]
