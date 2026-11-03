from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import IsShopOperator
from .models import Shop
from .serializers import ShopCreateSerializer, ShopProfileSerializer, ShopSerializer


class ShopCreateView(APIView):
    permission_classes = [IsAuthenticated, IsShopOperator]

    def post(self, request):
        serializer = ShopCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                shop = Shop.objects.create(operator_user_id=request.user.id, **serializer.validated_data)
        except IntegrityError:
            # The operator already has a shop (a retry or a concurrent request).
            # Return it unchanged: never overwrite its name or approval status.
            shop = Shop.objects.get(operator_user_id=request.user.id)
            return Response(ShopSerializer(shop).data, status=status.HTTP_200_OK)
        return Response(ShopSerializer(shop).data, status=status.HTTP_201_CREATED)


class MyShopView(APIView):
    """The caller's own shop. There is no shop ID in the URL: the shop is
    always found from the operator ID in the verified token, so one operator
    cannot read or edit another operator's shop."""

    permission_classes = [IsAuthenticated, IsShopOperator]

    def get_own_shop(self, request):
        return Shop.objects.filter(operator_user_id=request.user.id).first()

    def get(self, request):
        shop = self.get_own_shop(request)
        if shop is None:
            return Response({"detail": "You have no shop yet."}, status=status.HTTP_404_NOT_FOUND)
        return Response(ShopSerializer(shop).data)

    def patch(self, request):
        shop = self.get_own_shop(request)
        if shop is None:
            return Response({"detail": "You have no shop yet."}, status=status.HTTP_404_NOT_FOUND)
        serializer = ShopProfileSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        for field, value in serializer.validated_data.items():
            setattr(shop, field, value)
        # Save only the edited columns, so an approval decision recorded at the
        # same moment (LP-9) is never overwritten by a profile edit.
        shop.save(update_fields=[*serializer.validated_data, "updated_at"])
        return Response(ShopSerializer(shop).data)
