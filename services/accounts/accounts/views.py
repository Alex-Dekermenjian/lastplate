from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from .serializers import ShopOperatorRegistrationSerializer, SignInSerializer, UserSerializer
from .throttles import SignInEmailThrottle


class ShopOperatorRegisterView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_register"

    def post(self, request):
        serializer = ShopOperatorRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        refresh = SignInSerializer.get_token(user)
        return Response(
            {
                "user": UserSerializer(user).data,
                "refresh": str(refresh),
                "access": str(refresh.access_token),
            },
            status=status.HTTP_201_CREATED,
        )


class SignInView(TokenObtainPairView):
    serializer_class = SignInSerializer
    throttle_classes = [ScopedRateThrottle, SignInEmailThrottle]
    throttle_scope = "auth_sign_in"


class MeView(APIView):
    def get(self, request):
        return Response(UserSerializer(request.user).data)
