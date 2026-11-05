
"""
URL configuration for the web frontend.

https://docs.djangoproject.com/en/5.2/topics/http/urls/
"""

from django.urls import path

from frontend import account_views, browse_views, health, listing_views, views

urlpatterns = [
    path("", views.home, name="home"),

    # LP-15: Guided Sprint 1 demonstration (development only).
    path("demo/", views.demo_guide, name="demo-guide"),

    # LP-13: Public food browsing (no sign-in).
    path("offers/", browse_views.browse_offers, name="browse-offers"),
    path(
        "offers/<int:listing_id>/",
        browse_views.offer_detail,
        name="offer-detail",
    ),

    # One sign-up: first choose customer or restaurant owner.
    path("sign-up/", account_views.sign_up, name="sign-up"),
    path("sign-up/customer/", account_views.customer_sign_up, name="customer-sign-up"),
    path("register/", account_views.register, name="register"),
    path("sign-in/", account_views.sign_in, name="sign-in"),
    path("sign-out/", account_views.sign_out, name="sign-out"),

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
