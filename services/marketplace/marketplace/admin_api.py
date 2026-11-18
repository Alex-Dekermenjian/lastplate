"""LP-57: the admin API behind the /console pages.

Every endpoint accepts only Accounts access tokens with role ADMIN. Customers
and shop operators get 403; a missing or invalid token gets 401.
"""

from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .authentication import IsAdmin
from .browse import visible_listings
from .models import Listing, Shop
from .moderation import ModerationError, remove_listing, restore_shop, suspend_shop
from .stock import free_quantity, with_committed
from .serializers import PublicBranchSerializer, RejectUnknownFieldsMixin
from .shop_review import ShopReviewError, review_shop

Status = Shop.ApprovalStatus
ADMIN_ONLY = [IsAuthenticated, IsAdmin]


class AdminShopSerializer(serializers.ModelSerializer):
    profile_complete = serializers.BooleanField(read_only=True)
    # LP-68: one approval covers every branch, so the admin sees them all.
    branches = serializers.SerializerMethodField()

    def get_branches(self, obj):
        return PublicBranchSerializer(obj.branches.filter(removed_at__isnull=True), many=True).data

    class Meta:
        model = Shop
        fields = [
            "id", "operator_user_id", "name", "address", "area", "public_contact", "profile_complete",
            "approval_status", "rejection_reason", "reviewed_by_user_id", "reviewed_at", "created_at", "updated_at",
            "branches",
            # LP-45: asked for review again after a rejection.
            "review_requests", "last_rejection_reason", "review_request_note", "review_requested_at",
            # LP-60: suspended by the admin, and why.
            "suspended_at", "suspension_reason", "suspended_by_user_id",
        ]
        read_only_fields = fields


class ShopDecisionSerializer(RejectUnknownFieldsMixin, serializers.Serializer):
    # Pending is not a decision: a reviewed shop never goes back to it.
    decision = serializers.ChoiceField(choices=[Status.APPROVED, Status.REJECTED])
    reason = serializers.CharField(max_length=2000, required=False, allow_blank=True, default="")
    identity_confirmed = serializers.BooleanField(required=False, default=False)


@api_view(["GET"])
@permission_classes(ADMIN_ONLY)
def summary(request):
    counts = {value: 0 for value in Status.values}
    for row in Shop.objects.values("approval_status").order_by():
        counts[row["approval_status"]] += 1
    return Response({
        "pending_shops": counts[Status.PENDING],
        "approved_shops": counts[Status.APPROVED],
        "rejected_shops": counts[Status.REJECTED],
        # Live offers are exactly what customers can browse right now.
        "live_offers": len(visible_listings()),
    })


@api_view(["GET"])
@permission_classes(ADMIN_ONLY)
def shop_list(request):
    approval_status = request.query_params.get("status", Status.PENDING).upper()
    if approval_status not in Status.values:
        return Response(
            {"status": ["Choose PENDING, APPROVED or REJECTED."]},
            status=status.HTTP_400_BAD_REQUEST,
        )
    # Oldest application first, so nobody waits longest by accident.
    shops = Shop.objects.filter(approval_status=approval_status).order_by("created_at", "id")
    # LP-45: ?resubmitted=1 lists only restaurants that asked for review again.
    if request.query_params.get("resubmitted") in ("1", "true"):
        shops = shops.filter(review_requests__gt=0)
    return Response(AdminShopSerializer(shops, many=True).data)


@api_view(["GET"])
@permission_classes(ADMIN_ONLY)
def shop_detail(request, pk):
    shop = Shop.objects.filter(pk=pk).first()
    if shop is None:
        return Response({"detail": "Shop not found."}, status=status.HTTP_404_NOT_FOUND)
    return Response(AdminShopSerializer(shop).data)


@api_view(["POST"])
@permission_classes(ADMIN_ONLY)
def shop_review(request, pk):
    serializer = ShopDecisionSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    try:
        shop = review_shop(pk, **serializer.validated_data, reviewer_user_id=request.user.id)
    except Shop.DoesNotExist:
        return Response({"detail": "Shop not found."}, status=status.HTTP_404_NOT_FOUND)
    except ShopReviewError as error:
        return Response(
            {"detail": "This decision isn't allowed.", "fields": error.errors},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return Response(AdminShopSerializer(shop).data)


# ---------------------------------------------------------------------------
# LP-60: suspend / restore a restaurant, see its offers, remove one offer.
# ---------------------------------------------------------------------------

class AdminListingSerializer(serializers.ModelSerializer):
    free_quantity = serializers.SerializerMethodField()

    def get_free_quantity(self, obj):
        return free_quantity(obj)

    class Meta:
        model = Listing
        fields = ["id", "title", "kind", "status", "discounted_price", "total_quantity", "free_quantity",
                  "pickup_start", "pickup_end", "removed_at", "removal_reason", "removed_by_user_id"]
        read_only_fields = fields


def _moderation_error(error):
    return Response({"code": error.code, "detail": error.detail}, status=error.http_status)


def _reason_from(request):
    return request.data.get("reason") if isinstance(request.data, dict) else None


@api_view(["POST"])
@permission_classes(ADMIN_ONLY)
def shop_suspend(request, pk):
    try:
        shop = suspend_shop(pk, _reason_from(request), request.user.id)
    except ModerationError as error:
        return _moderation_error(error)
    return Response(AdminShopSerializer(shop).data)


@api_view(["POST"])
@permission_classes(ADMIN_ONLY)
def shop_restore(request, pk):
    try:
        shop = restore_shop(pk)
    except ModerationError as error:
        return _moderation_error(error)
    return Response(AdminShopSerializer(shop).data)


@api_view(["GET"])
@permission_classes(ADMIN_ONLY)
def shop_listings(request, pk):
    if not Shop.objects.filter(pk=pk).exists():
        return Response({"detail": "Shop not found."}, status=status.HTTP_404_NOT_FOUND)
    # Drafts are the owner's business; published, ended and removed offers are shown.
    listings = (with_committed(Listing.objects.filter(shop_id=pk).exclude(status=Listing.Status.DRAFT))
                .order_by("-pickup_end", "-id")[:100])
    return Response(AdminListingSerializer(listings, many=True).data)


@api_view(["POST"])
@permission_classes(ADMIN_ONLY)
def listing_remove(request, pk):
    try:
        listing = remove_listing(pk, _reason_from(request), request.user.id)
    except ModerationError as error:
        return _moderation_error(error)
    return Response(AdminListingSerializer(listing).data)
