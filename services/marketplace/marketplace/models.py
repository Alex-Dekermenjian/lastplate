from django.conf import settings
from django.db import models
from django.db.models import Q


class Shop(models.Model):
    """A shop run by one operator account from the Accounts service.

    operator_user_id is the Accounts user ID; there is deliberately no
    cross-service ForeignKey. The operator edits the profile fields (LP-8);
    approval_status, rejection_reason and the review record are set only by a
    team admin in this service's Django admin (LP-9).
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
    # A local team-admin account in this service, kept so the decision stays
    # attributable: an account that has reviewed shops cannot be deleted.
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="+",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        permissions = [("review_shop", "Can approve or reject shops")]
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
