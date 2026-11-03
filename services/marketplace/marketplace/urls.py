from django.urls import path

from . import views

urlpatterns = [
    path("shops/", views.ShopCreateView.as_view(), name="shop-create"),
    path("shops/me/", views.MyShopView.as_view(), name="shop-me"),
]
