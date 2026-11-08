
from django.urls import path

from . import admin_api, views

urlpatterns = [
    # LP-57: admin API for /console (Admin tokens only)
    path("admin/summary", admin_api.summary, name="admin-summary"),
    path("admin/shops", admin_api.shop_list, name="admin-shops"),
    path("admin/shops/<int:pk>", admin_api.shop_detail, name="admin-shop-detail"),
    path("admin/shops/<int:pk>/review", admin_api.shop_review, name="admin-shop-review"),

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

    # LP-12: Stock adjustments (POST) and their history (GET)
    path(
        "listings/<int:pk>/stock-adjustments",
        views.listing_stock_adjustments,
        name="listing_stock_adjustments",
    ),
]
