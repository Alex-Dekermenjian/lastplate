
from django.urls import path
from . import views

app_name = "marketplace"

urlpatterns = [
    path(
        "listings/new/",
        views.create_listing,
        name="create_listing",
    ),
    path(
        "listings/<int:pk>/edit/",
        views.edit_listing,
        name="edit_listing",
    ),
    path(
        "listings/success/",
        views.listing_success,
        name="listing_success",
    ),
]
