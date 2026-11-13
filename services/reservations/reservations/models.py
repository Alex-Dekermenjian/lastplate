"""LP-17: orders, their items and their history.

Reservations owns these tables and nothing else. Customers, restaurants,
branches and listings belong to other services, so they are stored as plain
IDs, never as a ForeignKey (docs/microservices-plan.md §3).

Rules kept here, whatever code calls them:
- orders, order lines and history rows are never deleted;
- an order's status changes only through reservations.orders.change_status,
  which writes the history row in the same transaction;
- an order line is a copy of the offer when the order was placed and is never
  edited afterwards, so later edits by the restaurant do not change it.
"""

import uuid

from django.db import models
from django.db.models import Q
from django.utils import timezone

# Pickup codes: 6 characters from letters and digits, without 0/O/1/I, which
# are easy to confuse when read out at the counter.
PICKUP_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PICKUP_CODE_LENGTH = 6
PICKUP_CODE_REGEX = r"^[A-HJ-NP-Z2-9]{6}$"


class NotDeletable(Exception):
    """Raised when code tries to delete an order, an order line or history."""


class StatusChangeRefused(Exception):
    """Raised when a status change does not go through change_status."""


class Status(models.TextChoices):
    # Labels are the words customers and shops see (docs/microservices-plan.md
    # §6). PROCESSING and NOT_RESERVED are internal and never shown as orders.
    PROCESSING = "PROCESSING", "Processing"
    CONFIRMED = "CONFIRMED", "Reserved"
    NOT_RESERVED = "NOT_RESERVED", "Not reserved"
    CANCELLED = "CANCELLED", "Cancelled"
    COLLECTED = "COLLECTED", "Paid"
    EXPIRED = "EXPIRED", "Expired"


# The four statuses customers and shops ever see, with their wording.
VISIBLE_STATUSES = {
    Status.CONFIRMED: "Reserved",
    Status.COLLECTED: "Paid",
    Status.CANCELLED: "Cancelled",
    Status.EXPIRED: "Expired",
}
# Orders that still hold a pickup code at the counter.
OPEN_STATUSES = (Status.PROCESSING, Status.CONFIRMED)


class SyncState(models.TextChoices):
    """Internal only: whether Marketplace agrees with status (§6)."""

    ALLOCATE_PENDING = "ALLOCATE_PENDING", "Allocation call not yet answered"
    RELEASE_PENDING = "RELEASE_PENDING", "Release not yet acknowledged"
    CONSUME_PENDING = "CONSUME_PENDING", "Consume not yet acknowledged"
    SHOP_CANCEL_PENDING = "SHOP_CANCEL_PENDING", "Shop-cancel not yet acknowledged"
    SYNCED = "SYNCED", "Synced with Marketplace"
    NEEDS_ATTENTION = "NEEDS_ATTENTION", "Needs staff attention"


class CancelledBy(models.TextChoices):
    CUSTOMER = "CUSTOMER", "Customer"
    SHOP = "SHOP", "Restaurant"
    ADMIN = "ADMIN", "Admin"


class Actor(models.TextChoices):
    CUSTOMER = "CUSTOMER", "Customer"
    SHOP = "SHOP", "Restaurant"
    ADMIN = "ADMIN", "Admin"
    SYSTEM = "SYSTEM", "System"


class NoDeleteQuerySet(models.QuerySet):
    def delete(self):
        raise NotDeletable(f"{self.model.__name__} rows are never deleted.")

    delete.queryset_only = True


class OrderQuerySet(NoDeleteQuerySet):
    def update(self, **kwargs):
        if "status" in kwargs:
            raise StatusChangeRefused("Change an order's status with reservations.orders.change_status.")
        return super().update(**kwargs)

    def visible_to_users(self):
        """Orders customers and shops see: never PROCESSING or NOT_RESERVED."""
        return self.filter(status__in=list(VISIBLE_STATUSES))


class Order(models.Model):
    """One checkout from one branch of one restaurant."""

    # Generated here and stored before any call to another service (§6).
    reservation_ref = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    # The customer's retry key for one checkout; retries are handled by LP-21.
    request_key = models.CharField(max_length=64)

    # Plain IDs owned by other services: Accounts user, Marketplace shop/branch.
    customer_user_id = models.PositiveBigIntegerField(db_index=True)
    shop_id = models.PositiveBigIntegerField(db_index=True)
    branch_id = models.PositiveBigIntegerField()

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROCESSING)
    sync_state = models.CharField(max_length=24, choices=SyncState.choices, default=SyncState.ALLOCATE_PENDING)

    pickup_code = models.CharField(max_length=PICKUP_CODE_LENGTH)
    # Paid in cash at pickup: the sum of the lines' quantity x unit price.
    total_cash = models.DecimalField(max_digits=10, decimal_places=2)

    created_at = models.DateTimeField(default=timezone.now, editable=False)
    pick_up_by = models.DateTimeField()

    cancelled_by = models.CharField(max_length=10, choices=CancelledBy.choices, blank=True)
    cancel_reason = models.TextField(blank=True)
    collected_at = models.DateTimeField(null=True, blank=True)

    # LP-78: where to collect, copied from Marketplace at checkout. The README
    # rule: the agreed address is saved with the order and never changes
    # later. Empty for orders placed before LP-78.
    shop_name = models.CharField(max_length=200, blank=True, default="")
    branch_name = models.CharField(max_length=100, blank=True, default="")
    pickup_address = models.CharField(max_length=255, blank=True, default="")
    shop_phone = models.CharField(max_length=150, blank=True, default="")

    objects = OrderQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["customer_user_id", "request_key"], name="order_unique_request_key"),
            # The code a customer shows at the counter is unique among the
            # branch's open orders; closed orders may reuse it later.
            models.UniqueConstraint(
                fields=["branch_id", "pickup_code"],
                condition=Q(status__in=["PROCESSING", "CONFIRMED"]),
                name="order_unique_open_pickup_code_per_branch",
            ),
            models.CheckConstraint(condition=Q(pickup_code__regex=PICKUP_CODE_REGEX), name="order_pickup_code_format"),
            models.CheckConstraint(condition=Q(total_cash__gte=0), name="order_total_cash_not_negative"),
            # Cancelled orders always say who cancelled; others never do.
            models.CheckConstraint(
                condition=(Q(status="CANCELLED") & ~Q(cancelled_by=""))
                | (~Q(status="CANCELLED") & Q(cancelled_by="")),
                name="order_cancelled_by_matches_status",
            ),
            # Paid orders always have a collection time; others never do.
            models.CheckConstraint(
                condition=(Q(status="COLLECTED") & Q(collected_at__isnull=False))
                | (~Q(status="COLLECTED") & Q(collected_at__isnull=True)),
                name="order_collected_at_matches_status",
            ),
        ]
        indexes = [models.Index(fields=["branch_id", "status"], name="order_branch_status")]

    def __str__(self):
        return f"Order {self.pickup_code} ({self.get_status_display()})"

    @classmethod
    def from_db(cls, db, field_names, values):
        instance = super().from_db(db, field_names, values)
        instance._saved_status = instance.__dict__.get("status")
        return instance

    def save(self, *args, **kwargs):
        saved_status = getattr(self, "_saved_status", None)
        if saved_status is not None and self.status != saved_status and not getattr(self, "_status_change_allowed", False):
            raise StatusChangeRefused("Change an order's status with reservations.orders.change_status.")
        super().save(*args, **kwargs)
        self._saved_status = self.status

    def delete(self, *args, **kwargs):
        raise NotDeletable("Orders are never deleted.")

    @property
    def status_label(self):
        """The wording customers and shops see, or None if never shown."""
        return VISIBLE_STATUSES.get(self.status)


class OrderLine(models.Model):
    """One item of an order: a copy of the offer when the order was placed."""

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="lines")
    # Marketplace listing ID; a plain ID, never a ForeignKey.
    listing_id = models.PositiveBigIntegerField()
    quantity = models.PositiveIntegerField()
    title = models.CharField(max_length=200)
    unit_price = models.DecimalField(max_digits=8, decimal_places=2)
    pickup_start = models.DateTimeField()
    pickup_end = models.DateTimeField()

    objects = NoDeleteQuerySet.as_manager()

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["order", "listing_id"], name="orderline_one_line_per_listing"),
            models.CheckConstraint(condition=Q(quantity__gte=1), name="orderline_quantity_positive"),
            models.CheckConstraint(condition=Q(unit_price__gte=0), name="orderline_unit_price_not_negative"),
            models.CheckConstraint(condition=Q(pickup_end__gt=models.F("pickup_start")), name="orderline_window_ends_after_start"),
        ]

    def __str__(self):
        return f"{self.quantity} x {self.title}"

    @property
    def line_total(self):
        return self.quantity * self.unit_price

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise StatusChangeRefused("Order lines are a copy taken when the order was placed and are never edited.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise NotDeletable("Order lines are never deleted.")


class OrderEvent(models.Model):
    """One status change of an order: what, who, why and when."""

    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="events")
    # Blank only for the first row, written when the order is created.
    from_status = models.CharField(max_length=20, choices=Status.choices, blank=True)
    to_status = models.CharField(max_length=20, choices=Status.choices)
    actor = models.CharField(max_length=10, choices=Actor.choices)
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    objects = NoDeleteQuerySet.as_manager()

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.from_status or '-'} → {self.to_status} by {self.get_actor_display()}"

    def save(self, *args, **kwargs):
        if self.pk is not None and not self._state.adding:
            raise StatusChangeRefused("Order history is never edited.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise NotDeletable("Order history is never deleted.")
