
from django.core.exceptions import PermissionDenied


def require_shop_operator(user):
    if not user.is_authenticated:
        raise PermissionDenied(
            "You must log in to manage food offers."
        )

    if user.role != "SHOP_OPERATOR":
        raise PermissionDenied(
            "Only shop operators can manage food offers."
        )


def require_approved_shop(shop, user):
    require_shop_operator(user)

    if shop is None:
        raise PermissionDenied(
            "A shop profile is required."
        )

    if shop.operator_id != user.pk:
        raise PermissionDenied(
            "You do not own this shop."
        )

    if shop.approval_status != "APPROVED":
        raise PermissionDenied(
            "Your shop must be approved before publishing."
        )

    required_fields = (
        "name",
        "address",
        "area",
        "public_contact",
    )

    for field in required_fields:
        if not str(getattr(shop, field, "") or "").strip():
            raise PermissionDenied(
                "Complete your shop profile before publishing."
            )
