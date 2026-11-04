
from django.conf import settings
from django.db import models
from django.db.models import Q

from .image_validators import validate_listing_photo
from .upload_paths import listing_photo_upload_path


# LP-7, LP-8 and LP-9: Shop model
class Shop(models.Model):
    """
    A shop operated by a user from the Accounts service.

    operator_user_id refers to the Accounts user ID.
    No cross-service ForeignKey is used.

    Shop profile fields are managed by the operator.
    Approval and review records are managed by authorized team admins.
    """

    class ApprovalStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    operator_user_id = models.PositiveBigIntegerField(unique=True)
    name = models.CharField(max_length=150)

    # LP-8: Shop profile information
    address = models.CharField(
        "pickup address",
        max_length=255,
        blank=True,
    )
    area = models.CharField(max_length=100, blank=True)
    public_contact = models.CharField(max_length=150, blank=True)

    # LP-9: Shop approval
    approval_status = models.CharField(
        max_length=10,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.PENDING,
    )
    rejection_reason = models.TextField(blank=True)

    # LP-9: Record who reviewed the shop and when.
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        permissions = [
            ("review_shop", "Can approve or reject shops"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    approval_status__in=[
                        "PENDING",
                        "APPROVED",
                        "REJECTED",
                    ]
                ),
                name="marketplace_shop_valid_approval_status",
            ),
        ]

    PROFILE_FIELDS = (
        "name",
        "address",
        "area",
        "public_contact",
    )

    @property
    def profile_complete(self):
        return all(
            getattr(self, field).strip()
            for field in self.PROFILE_FIELDS
        )

    def __str__(self):
        return self.name


# LP-10 and LP-11: Food listing model
class Listing(models.Model):

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        PUBLISHED = "PUBLISHED", "Published"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"

    # Numeric reference to a Shop in Marketplace
    shop_id = models.PositiveBigIntegerField()

    title = models.CharField(max_length=200, blank=True)
    description = models.TextField(blank=True)
    category = models.CharField(max_length=100, blank=True)

    # LP-11: Optional food photo
    photo = models.ImageField(
        upload_to=listing_photo_upload_path,
        validators=[validate_listing_photo],
        null=True,
        blank=True,
    )

    # LP-11: Ingredients and allergens
    ingredients = models.TextField(blank=True, default="")
    allergens = models.TextField(blank=True, default="")

    original_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )

    discounted_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )

    total_quantity = models.PositiveIntegerField(default=0)

    pickup_address = models.CharField(
        max_length=255,
        blank=True,
    )

    pickup_start = models.DateTimeField(
        null=True,
        blank=True,
    )

    pickup_end = models.DateTimeField(
        null=True,
        blank=True,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    terms_revision = models.PositiveIntegerField(default=1)

    first_reserved_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title


# LP-12: Record of every change to a listing's allocated total stock
class StockAdjustment(models.Model):
    """
    One change to Listing.total_quantity, saved in the same transaction
    as the stock update itself.

    actor_user_id refers to the Accounts user ID. reservation_ref refers
    to a Reservations reference (Sprint 2). No cross-service ForeignKey.
    """

    class Kind(models.TextChoices):
        MANUAL = "MANUAL", "Manual"
        SHOP_CANCELLATION = "SHOP_CANCELLATION", "Shop cancellation"

    listing = models.ForeignKey(
        Listing,
        on_delete=models.PROTECT,
        related_name="stock_adjustments",
    )
    quantity_change = models.IntegerField()
    reason = models.TextField()
    kind = models.CharField(
        max_length=20,
        choices=Kind.choices,
        default=Kind.MANUAL,
    )
    reservation_ref = models.CharField(
        max_length=64,
        blank=True,
        default="",
    )
    actor_user_id = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(quantity_change=0),
                name="marketplace_stockadjustment_nonzero_change",
            ),
            models.CheckConstraint(
                condition=~Q(reason=""),
                name="marketplace_stockadjustment_reason_required",
            ),
            models.CheckConstraint(
                condition=Q(kind__in=["MANUAL", "SHOP_CANCELLATION"]),
                name="marketplace_stockadjustment_valid_kind",
            ),
            # A repeated shop cancellation must not record a second
            # adjustment for the same reservation.
            models.UniqueConstraint(
                fields=["reservation_ref"],
                condition=Q(kind="SHOP_CANCELLATION"),
                name="marketplace_stockadjustment_one_per_cancellation",
            ),
        ]

    def __str__(self):
        return f"{self.quantity_change:+d} on listing {self.listing_id}"
