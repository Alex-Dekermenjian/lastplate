"""LP-19: the Reservations API, mounted at /api/."""

from django.urls import path

from . import views

urlpatterns = [
    path("reservations", views.reservations, name="reservations"),
]
