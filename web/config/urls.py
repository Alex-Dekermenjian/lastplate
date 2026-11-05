
"""
URL configuration for the web frontend.

https://docs.djangoproject.com/en/5.2/topics/http/urls/
"""

from django.urls import path
from django.views.generic import RedirectView

from frontend import account_views, browse_views, health, listing_views, views

urlpatterns = [
    path("", views.home, name="home"),

    # LP-13: Public food browsing (no sign-in).
    path("offers/", browse_views.browse_offers, name="browse-offers"),
    path(
        "offers/<int:listing_id>/",
        browse_views.offer_detail,
        name="offer-detail",
    ),
    # LP-39: a restaurant's page with everything it has left.
    path("restaurants/<int:shop_id>/", browse_views.restaurant_detail, name="restaurant-detail"),

    # LP-56: one login page and one sign-up page for every kind of account.
    path("login/", account_views.login, name="login"),
    path("signup/", account_views.signup, name="signup"),
    path("sign-out/", account_views.sign_out, name="sign-out"),
    path("account/", account_views.my_account, name="my-account"),
    # Old addresses from LP-7; ?next= and other query strings are kept.
    path("sign-in/", RedirectView.as_view(pattern_name="login", query_string=True), name="sign-in"),
    path("register/", RedirectView.as_view(pattern_name="signup", query_string=True), name="register"),

    path("workspace/", account_views.workspace, name="workspace"),
    path("workspace/finish-setup/", account_views.finish_setup, name="finish-setup"),
    path("workspace/profile/", account_views.shop_profile, name="shop-profile"),

    # LP-10 and LP-11: Listing management.
    path(
        "workspace/listings/new/",
        listing_views.create_listing,
        name="create-listing",
    ),
    path(
        "workspace/listings/<int:listing_id>/edit/",
        listing_views.edit_listing,
        name="edit-listing",
    ),

    # LP-9 and LP-10: Publishing.
    path(
        "workspace/listings/<int:listing_id>/publish/",
        listing_views.publish_listing,
        name="publish-listing",
    ),

    # LP-12: Stock adjustments.
    path(
        "workspace/listings/<int:listing_id>/stock/",
        listing_views.adjust_stock,
        name="adjust-stock",
    ),

    path("health/live/", health.live, name="health-live"),
    path("health/ready/", health.ready, name="health-ready"),
]
