from django.db import IntegrityError, transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import IsShopOperator
from .models import Shop
from .serializers import ShopCreateSerializer, ShopSerializer


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
    permission_classes = [IsAuthenticated, IsShopOperator]

    def get(self, request):
        shop = Shop.objects.filter(operator_user_id=request.user.id).first()
        if shop is None:
            return Response({"detail": "You have no shop yet."}, status=status.HTTP_404_NOT_FOUND)
        return Response(ShopSerializer(shop).data)
