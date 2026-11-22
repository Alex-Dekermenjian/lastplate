
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db.models import Q

from .image_validators import validate_listing_photo
from .upload_paths import (
    listing_blurred_photo_upload_path,
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

    # LP-45: a rejected owner fixed their details and asked for review
    # again. The admin sees the earlier reason and the owner's note.
    review_requests = models.PositiveIntegerField(default=0)
    last_rejection_reason = models.TextField(blank=True, default="")
    review_request_note = models.TextField(blank=True, default="")
    review_requested_at = models.DateTimeField(null=True, blank=True)

    # LP-60: the LastPlate admin suspended this restaurant. Its offers leave
    # the site and nothing new can be reserved; the owner sees the reason.
    # Existing Reserved orders stay valid. Restore clears all three.
    suspended_at = models.DateTimeField(null=True, blank=True)
    suspension_reason = models.TextField(blank=True, default="", db_default="")
    suspended_by_user_id = models.PositiveBigIntegerField(null=True, blank=True)

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
    # LP-29: when this branch closes (Lebanon time). A new offer's pickup end
    # defaults to it, and a pickup end after it needs the owner's confirm.
    # Empty: not set, so nothing is checked.
    closing_time = models.TimeField(null=True, blank=True)
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

    # LP-54: how customers see the photo. The owner always sees the real one.
    class PhotoEffect(models.TextChoices):
        NORMAL = "NORMAL", "Normal"
        BLURRED = "BLURRED", "Blurred"
        MAGIC = "MAGIC", "Blurred with magic"

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

    # LP-54: Normal, Blurred, or Blurred with magic (one tap to peek). Only
    # a LastBag can be blurred, and only when its owner chooses it.
    photo_effect = models.CharField(
        max_length=10,
        choices=PhotoEffect.choices,
        default=PhotoEffect.NORMAL,
        db_default=PhotoEffect.NORMAL,
    )
    # LP-54: the strongly blurred copy customers get for Blurred and Magic
    # offers. Made by the server (photo_effects.py), never uploaded.
    photo_blurred = models.ImageField(
        upload_to=listing_blurred_photo_upload_path,
        blank=True,
        default="",
        db_default="",
        editable=False,
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

    # LP-84: the most units of this offer one customer may reserve in total
    # (across all their orders). Empty means no limit.
    max_per_customer = models.PositiveIntegerField(null=True, blank=True)

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

    # LP-60: the LastPlate admin removed this offer (status WITHDRAWN) and
    # why; the owner sees the reason. Existing reservations stay valid.
    removed_at = models.DateTimeField(null=True, blank=True)
    removal_reason = models.TextField(blank=True, default="", db_default="")
    removed_by_user_id = models.PositiveBigIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(kind__in=["LASTBAG", "ITEM"]),
                name="marketplace_listing_valid_kind",
            ),
            # LP-84: a limit is at least 1 (empty means no limit).
            models.CheckConstraint(
                condition=Q(max_per_customer__isnull=True) | Q(max_per_customer__gte=1),
                name="marketplace_listing_max_per_customer_positive",
            ),
            models.CheckConstraint(
                condition=Q(photo_effect__in=["NORMAL", "BLURRED", "MAGIC"]),
                name="marketplace_listing_valid_photo_effect",
            ),
            # LP-54: only a LastBag's photo can be blurred; items show theirs as is.
            models.CheckConstraint(
                condition=Q(photo_effect="NORMAL") | Q(kind="LASTBAG"),
                name="marketplace_listing_photo_effect_only_for_lastbags",
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


# LP-20: stock committed to one reservation (docs/microservices-plan.md §6)
class Allocation(models.Model):
    """
    Marketplace's record of the units it holds or sold for one reservation.

    reservation_ref is the Reservations reference (a UUID it creates before
    calling). It is unique, so a repeated call always finds the same row and
    never takes stock twice. Only HELD moves; every other status is final.
    HELD and CONSUMED count against free stock (stock.committed_quantity).
    """

    class Status(models.TextChoices):
        HELD = "HELD", "Held"
        CONSUMED = "CONSUMED", "Sold"
        RELEASED = "RELEASED", "Released"
        SHOP_CANCELLED = "SHOP_CANCELLED", "Cancelled by the restaurant"
        REJECTED = "REJECTED", "Rejected"

    # Why a reserve call was refused. Stored, so a retry gets the same answer.
    class RejectReason(models.TextChoices):
        NOT_PUBLISHED = "not_published", "The offer is not published"
        SHOP_NOT_APPROVED = "shop_not_approved", "The restaurant is not approved"
        SHOP_SUSPENDED = "shop_suspended", "The restaurant is suspended"
        PICKUP_ENDED = "pickup_ended", "The pickup window has ended"
        TERMS_CHANGED = "terms_changed", "The offer changed since the customer saw it"
        NOT_ENOUGH_STOCK = "not_enough_stock", "Not enough left"

    # Statuses that count against free stock.
    COMMITTED = (Status.HELD, Status.CONSUMED)

    reservation_ref = models.UUIDField(unique=True)
    listing = models.ForeignKey(
        Listing,
        on_delete=models.PROTECT,
        related_name="allocations",
    )
    # Units asked for. 0 only for a release that arrived before any reserve
    # call (a "tombstone", so a late reserve call cannot take stock).
    quantity = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices)
    reject_reason = models.CharField(
        max_length=30,
        choices=RejectReason.choices,
        blank=True,
        default="",
    )

    # The offer's terms when the units were held, so later edits never
    # change what the customer agreed to (plan §6 step 4).
    title = models.CharField(max_length=200, blank=True)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    pickup_start = models.DateTimeField(null=True, blank=True)
    pickup_end = models.DateTimeField(null=True, blank=True)
    terms_revision = models.PositiveIntegerField(null=True, blank=True)
    shop_id = models.PositiveBigIntegerField()
    branch_id = models.PositiveBigIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    # When it left HELD (released, sold or cancelled by the restaurant).
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["listing", "status"], name="mkt_allocation_listing_status"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=["HELD", "CONSUMED", "RELEASED", "SHOP_CANCELLED", "REJECTED"]),
                name="marketplace_allocation_valid_status",
            ),
            # Units held or sold are always at least one.
            models.CheckConstraint(
                condition=~Q(status__in=["HELD", "CONSUMED", "SHOP_CANCELLED"]) | Q(quantity__gte=1),
                name="marketplace_allocation_held_quantity_positive",
            ),
            # A reason exactly when rejected.
            models.CheckConstraint(
                condition=(Q(status="REJECTED") & ~Q(reject_reason=""))
                | (~Q(status="REJECTED") & Q(reject_reason="")),
                name="marketplace_allocation_reason_only_when_rejected",
            ),
        ]

    def __str__(self):
        return f"{self.status} {self.quantity} of listing {self.listing_id} ({self.reservation_ref})"


# LP-54: a customer's one peek at a "Blurred with magic" photo. The point is
# kept as percentages of the photo's width and height, with the circular
# cut-out the server made there, so every reload shows the same circle.
class PhotoPeek(models.Model):
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="photo_peeks")
    customer_user_id = models.PositiveBigIntegerField()
    x = models.DecimalField(max_digits=5, decimal_places=2)
    y = models.DecimalField(max_digits=5, decimal_places=2)
    # The cut-out's diameter, as a percentage of the photo's width.
    size = models.DecimalField(max_digits=5, decimal_places=2)
    # A small PNG, transparent outside the circle.
    cutout = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["listing", "customer_user_id"],
                name="marketplace_one_peek_per_customer_per_offer",
            ),
            models.CheckConstraint(
                condition=Q(x__gte=0, x__lte=100, y__gte=0, y__lte=100),
                name="marketplace_photopeek_point_inside_photo",
            ),
        ]

    def __str__(self):
        return f"Peek by customer {self.customer_user_id} at listing {self.listing_id} ({self.x}%, {self.y}%)"
