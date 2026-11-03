from django.db import models
from django.db.models import Q


class Shop(models.Model):
    """A shop run by one operator account from the Accounts service.

    operator_user_id is the Accounts user ID; there is deliberately no
    cross-service ForeignKey. Profile details and approval decisions are
    added by LP-8 and LP-9.
    """

    class ApprovalStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    operator_user_id = models.PositiveBigIntegerField(unique=True)
    name = models.CharField(max_length=150)
    approval_status = models.CharField(
        max_length=10,
        choices=ApprovalStatus.choices,
        default=ApprovalStatus.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(approval_status__in=["PENDING", "APPROVED", "REJECTED"]),
                name="marketplace_shop_valid_approval_status",
            ),
        ]

    def __str__(self):
        return self.name
