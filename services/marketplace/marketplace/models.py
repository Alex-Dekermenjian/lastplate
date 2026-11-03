
from django.db import models
from django.db.models import Q

from .image_validators import validate_listing_photo
from .upload_paths import listing_photo_upload_path


# LP-7 and LP-8: Shop model
class Shop(models.Model):
    """
    A shop operated by a user from the Accounts service.

    operator_user_id refers to the Accounts user ID.
    No cross-service ForeignKey is used.
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

    # Shop approval information
    approval_status = models.CharField(
        max_length=10,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.PENDING,
    )
    rejection_reason = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
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
