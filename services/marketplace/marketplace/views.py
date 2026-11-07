
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.http import Http404
from django.views.decorators.csrf import csrf_exempt

from rest_framework import status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    parser_classes,
    permission_classes,
)
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import IsShopOperator
from .browse import visible_listing, visible_listings, visible_shop
from .listing_service import (
    save_listing_draft,
    publish_listing as publish_listing_service,
)
from .models import Listing, Shop
from .photo_service import replace_listing_photo
from .shop_service import (
    ApplicationClosed,
    delete_replaced_images,
    image_names,
    submit_application,
)
from .serializers import (
    LASTBAG,
    ListingSerializer,
    ShopApplicationSerializer,
    PublicListingDetailSerializer,
    PublicShopDetailSerializer,
    RestaurantOfferSerializer,
    PublicListingSerializer,
    ShopCreateSerializer,
    ShopProfileSerializer,
    ShopSerializer,
    StockAdjustmentRequestSerializer,
    StockAdjustmentSerializer,
)
from .stock_service import StockAdjustmentError, adjust_stock


# --------------------------------------------------
# LP-7: Shop creation
# --------------------------------------------------

class ShopCreateView(APIView):
    permission_classes = [IsAuthenticated, IsShopOperator]

    def post(self, request):
        serializer = ShopCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            with transaction.atomic():
                shop = Shop.objects.create(
                    operator_user_id=request.user.id,
                    **serializer.validated_data,
                )
        except IntegrityError:
            # Never overwrite an existing shop.
            shop = Shop.objects.get(
                operator_user_id=request.user.id
            )
            return Response(
                ShopSerializer(shop).data,
                status=status.HTTP_200_OK,
            )

        return Response(
            ShopSerializer(shop).data,
            status=status.HTTP_201_CREATED,
        )


# --------------------------------------------------
# LP-7 and LP-8: Shop profile
# --------------------------------------------------

class MyShopView(APIView):
    """
    Operators can only view and edit their own shop.

    Shop ownership is determined from the verified JWT.
    """
    permission_classes = [IsAuthenticated, IsShopOperator]

    def get_own_shop(self, request):
        return Shop.objects.filter(
            operator_user_id=request.user.id
        ).first()

    def get(self, request):
        shop = self.get_own_shop(request)

        if shop is None:
            return Response(
                {"detail": "You have no shop yet."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(ShopSerializer(shop).data)

    def patch(self, request):
        shop = self.get_own_shop(request)

        if shop is None:
            return Response(
                {"detail": "You have no shop yet."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = ShopProfileSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        old_images = image_names(shop)

        for field, value in serializer.validated_data.items():
            setattr(shop, field, value)

        # Update only the edited profile fields.
        # Never overwrite a simultaneous approval decision.
        shop.save(
            update_fields=[
                *serializer.validated_data,
                "updated_at",
            ]
        )
        delete_replaced_images(shop, old_images)

        return Response(ShopSerializer(shop).data)


# --------------------------------------------------
# LP-37: A restaurant's request to join LastPlate
# --------------------------------------------------

ALREADY_REVIEWED = (
    "Your restaurant has already been reviewed, so its request "
    "can't be changed. Please contact the LastPlate team."
)


class ShopApplicationView(APIView):
    """
    Send the three-step request. Creates the owner's shop, or updates it
    while it is still Pending, and records when the conditions were
    accepted. The shop is always found from the verified token.
    """
    permission_classes = [IsAuthenticated, IsShopOperator]

    def post(self, request):
        shop = Shop.objects.filter(
            operator_user_id=request.user.id
        ).first()

        if shop is not None and shop.approval_status != Shop.ApprovalStatus.PENDING:
            return Response(
                {"detail": ALREADY_REVIEWED},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = ShopApplicationSerializer(data=request.data, shop=shop)
        serializer.is_valid(raise_exception=True)

        data = dict(serializer.validated_data)
        data.pop("accept_conditions")

        try:
            shop, created = submit_application(request.user.id, data)
        except ApplicationClosed:
            return Response(
                {"detail": ALREADY_REVIEWED},
                status=status.HTTP_409_CONFLICT,
            )

        return Response(
            ShopSerializer(shop).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


# --------------------------------------------------
# LP-10: Listing creation
# --------------------------------------------------

@api_view(["POST"])
@permission_classes([IsAuthenticated, IsShopOperator])
def create_listing(request):
    shop = Shop.objects.filter(
        operator_user_id=request.user.id
    ).first()

    if shop is None:
        return Response(
            {"detail": "Create a shop before adding listings."},
            status=status.HTTP_403_FORBIDDEN,
        )

    # The browser must never choose ownership or status.
    if "shop_id" in request.data or "status" in request.data:
        return Response(
            {"detail": "shop_id and status cannot be supplied."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    listing = save_listing_draft(
        data=request.data,
        shop_id=shop.id,
        shop=shop,
    )

    return Response(
        ListingSerializer(listing).data,
        status=status.HTTP_201_CREATED,
    )


# --------------------------------------------------
# LP-10: Listing editing
# --------------------------------------------------

@api_view(["PATCH"])
@permission_classes([IsAuthenticated, IsShopOperator])
def edit_listing(request, pk):
    shop = Shop.objects.filter(
        operator_user_id=request.user.id
    ).first()

    if shop is None:
        return Response(
            {"detail": "You have no shop."},
            status=status.HTTP_403_FORBIDDEN,
        )

    listing = Listing.objects.filter(
        pk=pk,
        shop_id=shop.id,
        status=Listing.Status.DRAFT,
    ).first()

    if listing is None:
        return Response(
            {"detail": "Draft listing not found."},
            status=status.HTTP_404_NOT_FOUND,
        )

    protected_fields = {
        "shop_id",
        "status",
        "id",
        "terms_revision",
        "first_reserved_at",
        "created_at",
        "updated_at",
        "photo",
    }

    forbidden = protected_fields.intersection(
        request.data.keys()
    )

    if forbidden:
        return Response(
            {
                "detail": "These fields cannot be edited.",
                "fields": sorted(forbidden),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    serializer = ListingSerializer(
        listing,
        data=request.data,
        partial=True,
        context={"shop": shop},
    )
    serializer.is_valid(raise_exception=True)
    serializer.save()

    return Response(
        serializer.data,
        status=status.HTTP_200_OK,
    )


# --------------------------------------------------
# LP-10: Listing publication
# --------------------------------------------------


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsShopOperator])
def publish_listing(request, pk):
    # Never accept approval flags from the browser.
    if request.data:
        return Response(
            {"detail": "Publication does not accept request fields."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Get the shop belonging to the authenticated operator.
    shop = Shop.objects.filter(
        operator_user_id=request.user.id
    ).first()

    if shop is None:
        return Response(
            {"detail": "You have no shop."},
            status=status.HTTP_403_FORBIDDEN,
        )

    # Verify ownership before proceeding.
    listing = Listing.objects.filter(pk=pk).first()

    if listing is None or listing.shop_id != shop.id:
        return Response(
            {"detail": "You do not own this listing."},
            status=status.HTTP_403_FORBIDDEN,
        )

    # Approval must come from the database.
    shop_approved = (
        shop.approval_status == Shop.ApprovalStatus.APPROVED
    )

    if not shop_approved:
        return Response(
            {"detail": "Your shop must be approved before publishing."},
            status=status.HTTP_403_FORBIDDEN,
        )

    # LP-8: Check actual profile completeness.
    profile_complete = shop.profile_complete

    if not profile_complete:
        return Response(
            {"detail": "Complete your shop profile before publishing."},
            status=status.HTTP_403_FORBIDDEN,
        )

    # LP-9 is integrated: publish using trusted shop approval
    # and profile information from the database.
    try:
        published = publish_listing_service(
        listing_id=pk,
        shop_id=shop.id,
        shop_approved=shop_approved,
        profile_complete=profile_complete,
    )
    except Listing.DoesNotExist:
        return Response(
        {"detail": "Listing not found."},
        status=status.HTTP_404_NOT_FOUND,
    )
    except DjangoValidationError as exc:
        body = {"detail": exc.messages if hasattr(exc, "messages") else str(exc)}
        if hasattr(exc, "message_dict"):
            body["fields"] = exc.message_dict
        return Response(body, status=status.HTTP_400_BAD_REQUEST)

    return Response(
        ListingSerializer(
            published,
            context={"request": request},
        ).data,
        status=status.HTTP_200_OK,
    )

# --------------------------------------------------
# LP-11: Secure photo upload and replacement
# --------------------------------------------------

@api_view(["POST"])
@permission_classes([IsAuthenticated, IsShopOperator])
@parser_classes([MultiPartParser, FormParser])
def upload_listing_photo(request, pk):
    shop = Shop.objects.filter(
        operator_user_id=request.user.id
    ).first()

    if shop is None:
        return Response(
            {"detail": "You have no shop."},
            status=status.HTTP_403_FORBIDDEN,
        )

    listing = Listing.objects.filter(
        pk=pk,
        shop_id=shop.id,
        status=Listing.Status.DRAFT,
    ).first()

    if listing is None:
        raise Http404("Draft listing not found.")

    if set(request.data.keys()) != {"photo"}:
        return Response(
            {"photo": ["Upload exactly one photo."]},
            status=status.HTTP_400_BAD_REQUEST,
        )

    uploaded_files = request.FILES.getlist("photo")

    if len(uploaded_files) != 1:
        return Response(
            {"photo": ["Upload exactly one photo."]},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        updated_listing = replace_listing_photo(
            listing_id=listing.pk,
            shop_id=shop.id,
            uploaded_file=uploaded_files[0],
        )
    except Listing.DoesNotExist:
        raise Http404("Draft listing not found.")
    except DjangoValidationError as exc:
        return Response(
            {"photo": exc.messages},
            status=status.HTTP_400_BAD_REQUEST,
        )

    return Response(
        ListingSerializer(
            updated_listing,
            context={"request": request},
        ).data,
        status=status.HTTP_200_OK,
    )

# LP-10 and LP-11: Retrieve the authenticated operator's listings
@api_view(["GET"])
@permission_classes([IsAuthenticated, IsShopOperator])
def my_listings(request):
    # Identify the shop using the verified JWT identity.
    shop = Shop.objects.filter(
        operator_user_id=request.user.id
    ).first()

    if shop is None:
        return Response(
            {"detail": "You have no shop."},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Never accept a shop ID from the browser.
    # Return drafts and published listings owned by this shop only.
    listings = Listing.objects.filter(
        shop_id=shop.id
    ).order_by("-created_at")

    serializer = ListingSerializer(
        listings,
        many=True,
        context={"request": request},
    )

    return Response(
        serializer.data,
        status=status.HTTP_200_OK,
    )


# --------------------------------------------------
# LP-12: Stock adjustments and their history
# --------------------------------------------------

def _error(code, detail, http_status, **extra):
    return Response(
        {"code": code, "detail": detail, **extra},
        status=http_status,
    )


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, IsShopOperator])
def listing_stock_adjustments(request, pk):
    # Ownership comes only from the verified JWT.
    shop = Shop.objects.filter(
        operator_user_id=request.user.id
    ).first()

    if shop is None:
        return _error(
            "no_shop",
            "You have no shop.",
            status.HTTP_403_FORBIDDEN,
        )

    if request.method == "GET":
        listing = Listing.objects.filter(pk=pk, shop_id=shop.id).first()

        if listing is None:
            return _error(
                "not_owner",
                "You do not own this listing.",
                status.HTTP_403_FORBIDDEN,
            )

        return Response(
            StockAdjustmentSerializer(
                listing.stock_adjustments.all(),
                many=True,
            ).data,
            status=status.HTTP_200_OK,
        )

    serializer = StockAdjustmentRequestSerializer(data=request.data)

    if not serializer.is_valid():
        return _error(
            "invalid",
            "Check the quantity change and reason.",
            status.HTTP_400_BAD_REQUEST,
            fields=serializer.errors,
        )

    try:
        listing, adjustment = adjust_stock(
            listing_id=pk,
            shop_id=shop.id,
            quantity_change=serializer.validated_data["quantity_change"],
            reason=serializer.validated_data["reason"],
            actor_user_id=request.user.id,
        )
    except StockAdjustmentError as exc:
        return _error(exc.code, exc.detail, exc.http_status)

    return Response(
        {
            "adjustment": StockAdjustmentSerializer(adjustment).data,
            "listing": ListingSerializer(
                listing,
                context={"request": request},
            ).data,
        },
        status=status.HTTP_201_CREATED,
    )


# --------------------------------------------------
# LP-13: Public marketplace (no sign-in)
# --------------------------------------------------

# Public pages ignore any Authorization header, so a stale token in the
# browser can never turn a public page into a 401.
@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def browse_listings(request):
    visible = visible_listings()

    serializer = PublicListingSerializer(
        [listing for listing, _shop in visible],
        many=True,
        context={
            "request": request,
            "shops": {shop.id: shop for _listing, shop in visible},
        },
    )

    return Response(serializer.data, status=status.HTTP_200_OK)


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def listing_offer(request, pk):
    # Drafts, withdrawn offers, unapproved shops, ended and sold-out offers
    # all look the same from outside: not found.
    visible = visible_listing(pk)

    if visible is None:
        return _error(
            "not_found",
            "This offer is not available.",
            status.HTTP_404_NOT_FOUND,
        )

    listing, shop = visible

    return Response(
        PublicListingDetailSerializer(
            listing,
            context={"request": request, "shops": {shop.id: shop}},
        ).data,
        status=status.HTTP_200_OK,
    )


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def restaurant_page(request, pk):
    """LP-39: one approved restaurant and everything it has left, LastBags
    first. Pending, rejected and unknown restaurants are all 404."""
    shop = visible_shop(pk)

    if shop is None:
        return _error(
            "not_found",
            "This restaurant is not available.",
            status.HTTP_404_NOT_FOUND,
        )

    listings = [listing for listing, _shop in visible_listings(shop_id=shop.id)]
    offers = RestaurantOfferSerializer(
        listings,
        many=True,
        context={"request": request, "shops": {shop.id: shop}},
    ).data
    # Stable sort: LastBags first, each group still ending soonest first.
    offers = sorted(offers, key=lambda offer: offer["kind"] != LASTBAG)

    return Response(
        {"shop": PublicShopDetailSerializer(shop).data, "offers": offers},
        status=status.HTTP_200_OK,
    )


# One URL serves both the public read and the operator's write. Each target
# view keeps its own authentication, permissions and CSRF handling.
@csrf_exempt
def listings_collection(request):
    if request.method == "GET":
        return browse_listings(request)
    return create_listing(request)


@csrf_exempt
def listing_resource(request, pk):
    if request.method == "GET":
        return listing_offer(request, pk)
    return edit_listing(request, pk)
