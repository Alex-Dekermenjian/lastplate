
from django.urls import path

from . import admin_api, photo_effect_views, views

urlpatterns = [
    # LP-57: admin API for /console (Admin tokens only)
    path("admin/summary", admin_api.summary, name="admin-summary"),
    path("admin/shops", admin_api.shop_list, name="admin-shops"),
    path("admin/shops/<int:pk>", admin_api.shop_detail, name="admin-shop-detail"),
    path("admin/shops/<int:pk>/review", admin_api.shop_review, name="admin-shop-review"),
    # LP-60: suspend / restore a restaurant; see and remove its offers.
    path("admin/shops/<int:pk>/suspend", admin_api.shop_suspend, name="admin-shop-suspend"),
    path("admin/shops/<int:pk>/restore", admin_api.shop_restore, name="admin-shop-restore"),
    path("admin/shops/<int:pk>/listings", admin_api.shop_listings, name="admin-shop-listings"),
    path("admin/listings/<int:pk>/remove", admin_api.listing_remove, name="admin-listing-remove"),

    # LP-7 and LP-8: Shop endpoints
    path(
        "shops/",
        views.ShopCreateView.as_view(),
        name="shop-create",
    ),
    # LP-68: the owner's branches
    path("shops/me/branches/", views.my_branches, name="my-branches"),
    # LP-45: a rejected restaurant asks for review again.
    path("shops/me/review-again", views.review_again, name="shop-review-again"),
    path("shops/me/branches/<int:pk>/", views.my_branch, name="my-branch"),
    # LP-37: the owner sends the request to join LastPlate
    path(
        "shops/me/application/",
        views.ShopApplicationView.as_view(),
        name="shop-application",
    ),
    path(
        "shops/me/",
        views.MyShopView.as_view(),
        name="shop-me",
    ),

    # LP-39: a restaurant's public page (anyone)
    path("shops/<int:pk>", views.restaurant_page, name="restaurant-page"),

    # LP-10 and LP-13: Listing endpoints. GET is the public marketplace
    # (anyone); POST creates and PATCH edits the operator's own draft.
    path(
        "listings",
        views.listings_collection,
        name="listings",
    ),
    path(
        "listings/mine",
        views.my_listings,
        name="my_listings",
    ),
    path(
        "listings/<int:pk>",
        views.listing_resource,
        name="listing_detail",
    ),
    path(
        "listings/<int:pk>/publish",
        views.publish_listing,
        name="publish_listing",
    ),

    # LP-11: Photo uploads
    path(
        "listings/<int:pk>/photo",
        views.upload_listing_photo,
        name="upload_listing_photo",
    ),

    # LP-54: the owner's real photo, whatever effect customers get.
    path(
        "listings/<int:pk>/photo/original",
        photo_effect_views.owner_original_photo,
        name="listing_original_photo",
    ),
    # LP-54: one magic peek per customer per offer (POST taps, GET reloads).
    path(
        "listings/<int:pk>/peek",
        photo_effect_views.listing_peek,
        name="listing_peek",
    ),
    path("peeks/mine", photo_effect_views.my_peeks, name="my_peeks"),

    # LP-58: the owner deletes one of their drafts.
    path(
        "listings/<int:pk>/delete",
        views.delete_listing_draft,
        name="listing_delete_draft",
    ),

    # LP-58: the owner takes every free unit off sale.
    path(
        "listings/<int:pk>/sold-out",
        views.mark_listing_sold_out,
        name="listing_sold_out",
    ),

    # LP-29: the owner takes a published offer down; reservations stay.
    path(
        "listings/<int:pk>/unpublish",
        views.unpublish_listing,
        name="listing_unpublish",
    ),

    # LP-12: Stock adjustments (POST) and their history (GET)
    path(
        "listings/<int:pk>/stock-adjustments",
        views.listing_stock_adjustments,
        name="listing_stock_adjustments",
    ),
]
