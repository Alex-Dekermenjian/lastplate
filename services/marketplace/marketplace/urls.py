
from django.urls import path
from . import views

app_name = "marketplace"

urlpatterns = [
    path(
        "listings",
        views.create_listing,
        name="create_listing",
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
]

