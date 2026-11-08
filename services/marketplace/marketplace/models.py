
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db.models import Q

from .image_validators import validate_listing_photo
from .upload_paths import (
    listing_photo_upload_path,
    shop_logo_upload_path,
    shop_photo_upload_path,
)


# LP-37: A restaurant's category. The stored value is the readable word
# itself, because pages (LP-39) show it exactly as stored.
class ShopCategory(models.TextChoices):
    BAKERY = "Bakery", "Bakery"
    CAFE = "Café", "Café"
    RESTAURANT = "Restaurant", "Restaurant"
    SUSHI = "Sushi", "Sushi"
    PASTRY = "Pastry", "Pastry"
    GROCERY = "Grocery", "Grocery"
    OTHER = "Other", "Other"


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

    Category = ShopCategory

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

    # LP-37: Restaurant application details
    category = models.CharField(
        max_length=20,
        choices=ShopCategory.choices,
        default=ShopCategory.OTHER,
    )
    latitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
    )
    longitude = models.DecimalField(
        max_digits=9,
        decimal_places=6,
        null=True,
        blank=True,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
    )
    photo = models.ImageField(
        "cover photo",
        upload_to=shop_photo_upload_path,
        validators=[validate_listing_photo],
        null=True,
        blank=True,
    )
    logo = models.ImageField(
        upload_to=shop_logo_upload_path,
        validators=[validate_listing_photo],
        null=True,
        blank=True,
    )
    # When the owner ticked "I accept LastPlate's conditions for
    # restaurants" and sent the request. Empty until then.
    conditions_accepted_at = models.DateTimeField(null=True, blank=True)

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
    # LP-57: an Accounts Admin who reviewed the shop in /console. A review
    # made in the Django admin sets reviewed_by instead.
    reviewed_by_user_id = models.PositiveBigIntegerField(
        null=True,
        blank=True,
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
            models.CheckConstraint(
                condition=Q(category__in=ShopCategory.values),
                name="marketplace_shop_valid_category",
            ),
        ]

    PROFILE_FIELDS = (
        "name",
        "address",
        "area",
        "public_contact",
    )

    @property
    def application_sent(self):
        """LP-37: the owner finished the three-step request."""
        return self.conditions_accepted_at is not None

    @property
    def profile_complete(self):
        return all(
            getattr(self, field).strip()
            for field in self.PROFILE_FIELDS
        )

    def __str__(self):
        return self.name


# LP-68: A restaurant's branches. Each branch works like its own store under
# the restaurant's name ("ZWZ · Hamra"): its own pickup address, offers and
# stock. Branch 1 is the restaurant's main address (Shop.address/area).
MAX_BRANCHES = 3


class Branch(models.Model):
    shop = models.ForeignKey(Shop, on_delete=models.CASCADE, related_name="branches")
    # What customers see after the restaurant's name, e.g. "Hamra".
    name = models.CharField(max_length=100)
    address = models.CharField(max_length=255, blank=True)
    # A place from the website's Lebanon list (LP-65).
    area = models.CharField(max_length=100, blank=True)
    # 1 is the main branch; the order owners and customers see.
    position = models.PositiveSmallIntegerField(default=1)
    # A removed branch is hidden but kept, so past offers keep their branch.
    removed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["position", "id"]
        indexes = [models.Index(fields=["shop", "removed_at"], name="marketplace_branch_active")]

    @property
    def is_active(self):
        return self.removed_at is None

    def __str__(self):
        return f"{self.shop.name} · {self.name}"


@receiver(post_save, sender=Shop, dispatch_uid="lp68_first_branch")
def give_new_shop_its_first_branch(sender, instance, created, raw=False, **kwargs):
    """Every restaurant has at least one branch, however it was created
    (application, Django admin, tests): branch 1 from its address."""
    if created and not raw and not instance.branches.exists():
        Branch.objects.create(
            shop=instance,
            name=(instance.area or "").strip()[:100] or "Main branch",
            address=instance.address or "",
            area=instance.area or "",
            position=1,
        )


# LP-10 and LP-11: Food listing model
class Listing(models.Model):

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        PUBLISHED = "PUBLISHED", "Published"
        WITHDRAWN = "WITHDRAWN", "Withdrawn"

    # LP-38: A LastBag is a surprise bag; an item is one specific food.
    class Kind(models.TextChoices):
        LASTBAG = "LASTBAG", "LastBag (surprise)"
        ITEM = "ITEM", "Specific item"

    # Numeric reference to a Shop in Marketplace
    shop_id = models.PositiveBigIntegerField()

    # LP-68: the branch this offer is at; its pickup address comes from it.
    # Empty only for offers made before branches existed and never moved.
    branch = models.ForeignKey(
        Branch,
        on_delete=models.PROTECT,
        related_name="listings",
        null=True,
        blank=True,
    )

    kind = models.CharField(
        max_length=10,
        choices=Kind.choices,
        default=Kind.ITEM,
        # Also a database default, so rows written without a kind (for
        # example by code from before LP-38) are specific items.
        db_default=Kind.ITEM,
    )
    # LP-38: What a LastBag usually holds. Customers never see exact
    # contents for a bag, only this. Required to publish a LastBag.
    typical_contents = models.TextField(blank=True, default="", db_default="")

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

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(kind__in=["LASTBAG", "ITEM"]),
                name="marketplace_listing_valid_kind",
            ),
        ]

    @property
    def is_lastbag(self):
        return self.kind == self.Kind.LASTBAG

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
