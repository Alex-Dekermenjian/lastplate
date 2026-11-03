
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response


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
