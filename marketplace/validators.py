
from django.core.exceptions import ValidationError
from django.utils import timezone


def validate_listing_data(
    original_price,
    discounted_price,
    stock,
    pickup_start,
    pickup_end,
    publishing=False,
):
    errors = {}

    # Original price
    if original_price is None:
        if publishing:
            errors["original_price"] = "Original price is required."
    elif original_price <= 0:
        errors["original_price"] = "Original price must be positive."

    # Discounted price
    if discounted_price is None:
        if publishing:
            errors["discounted_price"] = "Discounted price is required."
    elif discounted_price <= 0:
        errors["discounted_price"] = "Discounted price must be positive."

    # Discount must be lower than original price
    if (
        original_price is not None
        and discounted_price is not None
        and original_price > 0
        and discounted_price >= original_price
    ):
        errors["discounted_price"] = (
            "Discounted price must be less than original price."
        )

    # Stock validation
    if stock is None:
        if publishing:
            errors["total_quantity"] = "Stock is required."
    elif isinstance(stock, bool) or not isinstance(stock, int) or stock < 0:
        errors["total_quantity"] = "Stock must be a non-negative whole number."
    elif publishing and stock == 0:
        errors["total_quantity"] = "Published offers must have stock available."

    # Pickup times
    if publishing:
        if pickup_start is None:
            errors["pickup_start"] = "Pickup start is required."
        if pickup_end is None:
            errors["pickup_end"] = "Pickup end is required."

    if pickup_start is not None and timezone.is_naive(pickup_start):
        errors["pickup_start"] = "Pickup start must include a timezone."

    if pickup_end is not None and timezone.is_naive(pickup_end):
        errors["pickup_end"] = "Pickup end must include a timezone."

    if (
        pickup_start is not None
        and pickup_end is not None
        and timezone.is_aware(pickup_start)
        and timezone.is_aware(pickup_end)
    ):
        if pickup_start >= pickup_end:
            errors["pickup_end"] = "Pickup end must be after pickup start."

    if (
        publishing
        and pickup_end is not None
        and timezone.is_aware(pickup_end)
        and pickup_end <= timezone.now()
    ):
        errors["pickup_end"] = "Pickup end must be in the future."

    if errors:
        raise ValidationError(errors)
