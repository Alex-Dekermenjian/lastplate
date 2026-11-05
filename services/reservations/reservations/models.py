"""LP-28 (draft for LP-19): a customer's order from one restaurant.

LP-19 owns creating orders at checkout and may extend these models; LP-28
needs them to let the restaurant accept or reject. Other services' data is
referred to by ID only (customer, shop, listings).

Each order line holds its own stock in Marketplace (an Allocation whose
reservation_ref is the line's allocation_ref).
"""

import uuid

from django.db import models
from django.db.models import Q


class Order(models.Model):
    class Status(models.TextChoices):
        # New order; its stock is held; waiting for the restaurant.
        PENDING = "PENDING", "Waiting for the restaurant"
        ACCEPTED = "ACCEPTED", "Accepted"
        REJECTED = "REJECTED", "Rejected by the restaurant"
        CANCELLED = "CANCELLED", "Cancelled by the customer"
        COLLECTED = "COLLECTED", "Collected"

    class SyncState(models.TextChoices):
        SYNCED = "SYNCED", "Marketplace agrees"
        # Rejected or cancelled; Marketplace has not yet confirmed the release.
        RELEASE_PENDING = "RELEASE_PENDING", "Release not yet confirmed"
        NEEDS_ATTENTION = "NEEDS_ATTENTION", "Needs staff attention"

    ref = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    customer_user_id = models.PositiveBigIntegerField()
    customer_name = models.CharField(max_length=150, blank=True)
    shop_id = models.PositiveBigIntegerField()
    shop_name = models.CharField(max_length=150, blank=True)
    # Snapshot from Marketplace: only this restaurant account may decide.
    shop_operator_user_id = models.PositiveBigIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    sync_state = models.CharField(max_length=20, choices=SyncState.choices, default=SyncState.SYNCED)
    rejection_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["shop_operator_user_id", "status"])]
        constraints = [
            models.CheckConstraint(
                condition=Q(status__in=["PENDING", "ACCEPTED", "REJECTED", "CANCELLED", "COLLECTED"]),
                name="reservations_order_valid_status",
            ),
            models.CheckConstraint(
                condition=~Q(status="REJECTED") | ~Q(rejection_reason=""),
                name="reservations_order_rejection_has_reason",
            ),
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
