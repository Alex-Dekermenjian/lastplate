
from django.db import IntegrityError, transaction

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import IsShopOperator
from .models import Shop
from .serializers import ShopCreateSerializer, ShopSerializer
from .listing_service import save_listing_draft
from .models import Listing
from .serializers import ListingSerializer
from rest_framework.decorators import api_view, permission_classes
from .listing_service import publish_listing as publish_listing_service
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.decorators import parser_classes

from .photo_service import replace_listing_photo
# LP-7: Shop creation
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
            # An existing shop must not be overwritten.
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


# LP-7: Retrieve the authenticated operator's shop
class MyShopView(APIView):
    permission_classes = [IsAuthenticated, IsShopOperator]

    def get(self, request):
        shop = Shop.objects.filter(
            operator_user_id=request.user.id
        ).first()

        if shop is None:
            return Response(
                {"detail": "You have no shop yet."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(ShopSerializer(shop).data)


# LP-10: Temporary listing endpoints
def feature_not_ready():
    return Response(
        {
            "code": "FEATURE_NOT_READY",
            "detail": (
                "Listing operations are unavailable until "
                "authentication and shop permissions are integrated."
            ),
        },
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )




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

    if "shop_id" in request.data or "status" in request.data:
        return Response(
            {"detail": "shop_id and status cannot be supplied."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    listing = save_listing_draft(
        data=request.data,
        shop_id=shop.id,
    )

    return Response(
        ListingSerializer(listing).data,
        status=status.HTTP_201_CREATED,
    )




@api_view(["PATCH"])
@permission_classes([IsAuthenticated, IsShopOperator])
def edit_listing(request, pk):
    # Find the shop belonging to the authenticated operator.
    shop = Shop.objects.filter(
        operator_user_id=request.user.id
    ).first()

    if shop is None:
        return Response(
            {"detail": "You have no shop."},
            status=status.HTTP_403_FORBIDDEN,
        )

    # Only allow editing a draft owned by this shop.
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

    # Do not let the client change protected fields.
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

    forbidden = protected_fields.intersection(request.data.keys())

    if forbidden:
        return Response(
            {
                "detail": "These fields cannot be edited.",
                "fields": sorted(forbidden),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Validate only the supplied fields.
    serializer = ListingSerializer(
        listing,
        data=request.data,
        partial=True,
    )
    serializer.is_valid(raise_exception=True)
    serializer.save()

    return Response(serializer.data, status=status.HTTP_200_OK)



@api_view(["POST"])
@permission_classes([IsAuthenticated, IsShopOperator])
def publish_listing(request, pk):
    # Publication settings must never come from user input.
    if request.data:
        return Response(
            {"detail": "Publication does not accept request fields."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Identify the shop using the authenticated user.
    shop = Shop.objects.filter(
        operator_user_id=request.user.id
    ).first()

    if shop is None:
        return Response(
            {"detail": "You have no shop."},
            status=status.HTTP_403_FORBIDDEN,
        )

    # LP-8 profile completeness is not available yet.
    # Keep publishing disabled until it is implemented.
    profile_complete = False

    # Read approval from the database, never the request.
    shop_approved = (
        shop.approval_status == Shop.ApprovalStatus.APPROVED
    )

    listing = publish_listing_service(
        listing_id=pk,
        shop_id=shop.id,
        shop_approved=shop_approved,
        profile_complete=profile_complete,
    )

    return Response(
        ListingSerializer(listing).data,
        status=status.HTTP_200_OK,
    )



@api_view(["POST"])
@permission_classes([IsAuthenticated, IsShopOperator])
@parser_classes([MultiPartParser, FormParser])
def upload_listing_photo(request, pk):
    # Identify the shop using the authenticated operator.
    shop = Shop.objects.filter(
        operator_user_id=request.user.id
    ).first()

    if shop is None:
        return Response(
            {"detail": "You have no shop."},
            status=status.HTTP_403_FORBIDDEN,
        )

    # Only allow uploads to this shop's draft listings.
    listing = Listing.objects.filter(
        pk=pk,
        shop_id=shop.id,
        status=Listing.Status.DRAFT,
    ).first()

    if listing is None:
        raise Http404("Draft listing not found.")

    # Require exactly one photo and no other input fields.
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

