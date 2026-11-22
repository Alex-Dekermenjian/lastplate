from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from .serializers import (
    ChangePasswordSerializer,
    CustomerRegistrationSerializer,
    MeSerializer,
    ProfileUpdateSerializer,
    ShopOperatorRegistrationSerializer,
    SignInSerializer,
    UserSerializer,
)
from .throttles import PasswordChangeThrottle, SignInEmailThrottle


class ShopOperatorRegisterView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_register"
    serializer_class = ShopOperatorRegistrationSerializer

    def post(self, request):
        serializer = self.serializer_class(data=request.data)
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


class CustomerRegisterView(ShopOperatorRegisterView):
    """LP-56: customer sign-up. Shares the registration throttle."""

    serializer_class = CustomerRegistrationSerializer


class SignInView(TokenObtainPairView):
    serializer_class = SignInSerializer
    throttle_classes = [ScopedRateThrottle, SignInEmailThrottle]
    throttle_scope = "auth_sign_in"


class MeView(APIView):
    def get(self, request):
        return Response(MeSerializer(request.user).data)

    def patch(self, request):
        """LP-18: change your own display name."""
        serializer = ProfileUpdateSerializer(request.user, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(MeSerializer(request.user).data)


class ChangePasswordView(APIView):
    """LP-50: POST /api/users/me/password/ with the current and the new
    password. Used after an admin gave a temporary password: once changed,
    the temporary password no longer works and the account is unlocked."""

    throttle_classes = [PasswordChangeThrottle]

    def post(self, request):
        serializer = ChangePasswordSerializer(request.user, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(MeSerializer(request.user).data)