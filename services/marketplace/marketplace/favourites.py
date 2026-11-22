"""LP-82: a customer's favourite restaurants.

Only signed-in customers may use these endpoints: shop operators, the
LastPlate administrator and staff tokens are refused (403). The customer
always comes from the token, so nobody can read or change someone else's
favourites.

- POST /api/favourites/shops/{id}/toggle saves a restaurant, or removes it
  if it was already saved (pressing the heart twice removes it).
- GET /api/favourites/shops lists the saved restaurants customers can see.
- GET /api/favourites/offers lists today's offers from those restaurants,
  with exactly the public browse rules (browse.visible_listings): published,
  pickup window open now (so today's, and not over), units left, restaurant
  approved and not suspended.
"""

from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from .browse import visible_listings, visible_shop
from .models import Favourite, Shop
from .serializers import PublicListingSerializer, PublicShopCardSerializer

CUSTOMER = "CUSTOMER"


class IsCustomer(BasePermission):
    message = "Only customers can save favourite restaurants."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == CUSTOMER and not user.is_staff)


def _favourite_shop_ids(customer_user_id):
    """The customer's saved restaurants, most recently saved first."""
    return list(
        Favourite.objects.filter(customer_user_id=customer_user_id).values_list("shop_id", flat=True)
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsCustomer])
def toggle_favourite(request, pk):
    customer = request.user.id

    removed, _ = Favourite.objects.filter(customer_user_id=customer, shop_id=pk).delete()
    if removed:
        # Removing always works, even for a restaurant that has since been
        # suspended or rejected, so a customer can tidy their list.
        return Response({"shop_id": pk, "favourite": False}, status=status.HTTP_200_OK)

    # Only a restaurant customers can open may be saved. Pending, rejected,
    # suspended and unknown restaurants all look missing.
    if visible_shop(pk) is None:
        return Response(
            {"code": "not_found", "detail": "This restaurant is not available."},
            status=status.HTTP_404_NOT_FOUND,
        )

    try:
        with transaction.atomic():
            Favourite.objects.create(customer_user_id=customer, shop_id=pk)
    except IntegrityError:
        # A second press that arrived at the same moment already saved it.
        pass
    return Response({"shop_id": pk, "favourite": True}, status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsCustomer])
def my_favourite_shops(request):
    favourite_ids = _favourite_shop_ids(request.user.id)
    shops = Shop.objects.filter(
        id__in=favourite_ids,
        approval_status=Shop.ApprovalStatus.APPROVED,
        suspended_at__isnull=True,
    ).annotate(
        active_branch_count=Count("branches", filter=Q(branches__removed_at__isnull=True)),
    ).in_bulk()
    ordered = [shops[shop_id] for shop_id in favourite_ids if shop_id in shops]
    return Response(
        {"shops": PublicShopCardSerializer(ordered, many=True).data},
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsCustomer])
def my_favourite_offers(request):
    favourite_ids = set(_favourite_shop_ids(request.user.id))
    visible = [
        (listing, shop)
        for listing, shop in (visible_listings() if favourite_ids else [])
        if shop.id in favourite_ids
    ]
    offers = PublicListingSerializer(
        [listing for listing, _shop in visible],
        many=True,
        context={"request": request, "shops": {shop.id: shop for _listing, shop in visible}},
    ).data
    return Response(
        {"has_favourites": bool(favourite_ids), "offers": offers},
        status=status.HTTP_200_OK,
    )
