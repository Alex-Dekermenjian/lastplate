
from django.db import IntegrityError, transaction

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import IsShopOperator
from .models import Shop
from .serializers import ShopCreateSerializer, ShopSerializer


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
def create_listing(request):
    return feature_not_ready()


@api_view(["PATCH"])
def edit_listing(request, pk):
    return feature_not_ready()


@api_view(["POST"])
def publish_listing(request, pk):
    return feature_not_ready()
