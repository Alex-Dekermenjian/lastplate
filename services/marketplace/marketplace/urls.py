
from django.urls import path

from . import views

urlpatterns = [
    # LP-7 and LP-8: Shop endpoints
    path(
        "shops/",
        views.ShopCreateView.as_view(),
        name="shop-create",
    ),
    path(
        "shops/me/",
        views.MyShopView.as_view(),
        name="shop-me",
    ),

    # LP-10: Listing endpoints
    path(
        "listings",
        views.create_listing,
        name="create_listing",
    ),
    path(
        "listings/mine",
        views.my_listings,
        name="my_listings",
    ),
    path(
        "listings/<int:pk>",
        views.edit_listing,
        name="edit_listing",
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
