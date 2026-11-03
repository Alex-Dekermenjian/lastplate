from django.db import models
from django.db.models import Q


class Shop(models.Model):
    """A shop run by one operator account from the Accounts service.

    operator_user_id is the Accounts user ID; there is deliberately no
    cross-service ForeignKey. The operator edits the profile fields (LP-8);
    approval_status and rejection_reason are set by the approval workflow
    (LP-9) and are read-only to the operator.
    """

    class ApprovalStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    operator_user_id = models.PositiveBigIntegerField(unique=True)
    name = models.CharField(max_length=150)
    # Blank until the operator completes the profile after registration.
    address = models.CharField("pickup address", max_length=255, blank=True)
    area = models.CharField(max_length=100, blank=True)
    public_contact = models.CharField(max_length=150, blank=True)
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
                condition=Q(approval_status__in=["PENDING", "APPROVED", "REJECTED"]),
                name="marketplace_shop_valid_approval_status",
            ),
        ]

    PROFILE_FIELDS = ("name", "address", "area", "public_contact")

    @property
    def profile_complete(self):
        return all(getattr(self, field).strip() for field in self.PROFILE_FIELDS)

    def __str__(self):
        return self.name
