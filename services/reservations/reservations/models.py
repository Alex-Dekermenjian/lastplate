"""LP-28, shaped to LP-19: a customer's order from one restaurant.

LP-19 (checkout) creates orders from a one-restaurant cart (LP-62); LP-28
lets the restaurant accept or reject them. Field and status names follow
LP-19 and LP-22 in Jira; agree any change with both card owners.

- Line items (offer, quantity, price), each with its own stock hold in
  Marketplace (an Allocation whose reservation_ref is allocation_ref).
- One pickup code and one cash total per order.
- Starts as Placed / cash due.

Other services' data is referred to by ID only (customer, shop, listings).
"""

import secrets
import uuid

from django.db import models
from django.db.models import Q

# No 0/O or 1/I, so a code read aloud at the counter can't be confused.
PICKUP_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PICKUP_CODE_LENGTH = 6


def new_pickup_code():
    return "".join(secrets.choice(PICKUP_CODE_ALPHABET) for _ in range(PICKUP_CODE_LENGTH))


class Order(models.Model):
    class Status(models.TextChoices):
        # LP-22's timeline: Placed → Accepted → Ready → Collected, or Rejected.
        PLACED = "PLACED", "Placed"
        ACCEPTED = "ACCEPTED", "Accepted"
        READY = "READY", "Ready"
        COLLECTED = "COLLECTED", "Collected"
        REJECTED = "REJECTED", "Rejected"
        CANCELLED = "CANCELLED", "Cancelled"

    class PaymentStatus(models.TextChoices):
        CASH_DUE = "CASH_DUE", "Cash due at pickup"
        PAID = "PAID", "Paid"

    class SyncState(models.TextChoices):
        SYNCED = "SYNCED", "Marketplace agrees"
        # Rejected or cancelled; Marketplace has not yet confirmed the release.
        RELEASE_PENDING = "RELEASE_PENDING", "Release not yet confirmed"
        NEEDS_ATTENTION = "NEEDS_ATTENTION", "Needs staff attention"

    ref = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    customer_user_id = models.PositiveBigIntegerField()
    customer_name = models.CharField(max_length=150, blank=True)
    # LP-19: the same checkout submitted twice creates one order.
    request_key = models.CharField(max_length=64)

    shop_id = models.PositiveBigIntegerField()
    shop_name = models.CharField(max_length=150)
    shop_address = models.CharField(max_length=255, blank=True)
    # Snapshot from Marketplace: only this restaurant account may decide.
    shop_operator_user_id = models.PositiveBigIntegerField()

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PLACED)
    payment_method = models.CharField(max_length=10, default="CASH", editable=False)
    payment_status = models.CharField(max_length=10, choices=PaymentStatus.choices, default=PaymentStatus.CASH_DUE)
    pickup_code = models.CharField(max_length=PICKUP_CODE_LENGTH, unique=True, default=new_pickup_code)
    total = models.DecimalField(max_digits=10, decimal_places=2)

    sync_state = models.CharField(max_length=20, choices=SyncState.choices, default=SyncState.SYNCED)
    rejection_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["shop_operator_user_id", "status"])]
        constraints = [
            models.UniqueConstraint(
                fields=["customer_user_id", "request_key"], name="reservations_order_one_per_request_key",
            ),
            models.CheckConstraint(
                condition=Q(status__in=["PLACED", "ACCEPTED", "READY", "COLLECTED", "REJECTED", "CANCELLED"]),
                name="reservations_order_valid_status",
            ),
            models.CheckConstraint(
                condition=~Q(status="REJECTED") | ~Q(rejection_reason=""),
                name="reservations_order_rejection_has_reason",
            ),
            models.CheckConstraint(condition=Q(total__gte=0), name="reservations_order_total_not_negative"),
        ]

    def __str__(self):
        return f"Order {self.ref} ({self.status})"


class OrderLine(models.Model):
    order = models.ForeignKey(Order, on_delete=models.PROTECT, related_name="lines")
    listing_id = models.PositiveBigIntegerField()
    allocation_ref = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    # Agreed terms at order time; later listing edits never change them.
    title = models.CharField(max_length=200)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField()
    pickup_start = models.DateTimeField(null=True, blank=True)
    pickup_end = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(condition=Q(quantity__gt=0), name="reservations_orderline_positive_quantity"),
        ]

    def __str__(self):
        return f"{self.quantity} × {self.title}"
