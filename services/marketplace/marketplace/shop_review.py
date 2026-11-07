"""LP-9 and LP-57: the rules for approving or rejecting a shop.

One set of rules serves both places a shop is reviewed: the /console pages
(through the admin API) and the developer-only Django admin.

- Approving needs a complete profile and the confirmation that the applicant
  represents the shop. Approving an already approved shop changes nothing.
- Rejecting needs a reason; the owner sees it.
- A reviewed shop cannot go back to Pending.
"""

from django.db import transaction
from django.utils import timezone

from .models import Shop

Status = Shop.ApprovalStatus

INCOMPLETE_PROFILE = "This shop's profile is incomplete, so it cannot be approved."
CONFIRM_IDENTITY = "Confirm the applicant represents this shop before approving."
REASON_REQUIRED = "Give a reason. The shop owner will see it."
NO_RETURN_TO_PENDING = "A reviewed shop cannot return to Pending. Approve or reject it."
REASON_ONLY_WHEN_REJECTING = "Choose Rejected to give a reason."


class ShopReviewError(Exception):
    """The decision breaks a review rule. errors maps a field to its message."""

    def __init__(self, errors):
        super().__init__(errors)
        self.errors = errors


def review_errors(shop, decision, reason="", identity_confirmed=False):
    """Every rule the decision breaks, as {field: message}. Empty when allowed.

    Fields: approval_status, identity_confirmed and rejection_reason.
    """
    errors = {}
    reason = (reason or "").strip()

    if decision == Status.APPROVED:
        if shop.approval_status != Status.APPROVED:
            if not shop.profile_complete:
                errors["approval_status"] = INCOMPLETE_PROFILE
            if not identity_confirmed:
                errors["identity_confirmed"] = CONFIRM_IDENTITY
    elif decision == Status.REJECTED:
        if not reason:
            errors["rejection_reason"] = REASON_REQUIRED
    elif decision == Status.PENDING:
        if shop.approval_status != Status.PENDING:
            errors["approval_status"] = NO_RETURN_TO_PENDING
        elif reason:
            errors["rejection_reason"] = REASON_ONLY_WHEN_REJECTING
    else:
        errors["approval_status"] = "Choose Approved or Rejected."

    return errors


def record_review(shop, decision, reason, *, reviewer=None, reviewer_user_id=None):
    """Save an allowed decision and who made it. Only the decision fields are
    written, so a profile edit made at the same moment survives."""
    shop.approval_status = decision
    shop.rejection_reason = (reason or "").strip() if decision == Status.REJECTED else ""
    shop.reviewed_by = reviewer
    shop.reviewed_by_user_id = reviewer_user_id
    shop.reviewed_at = timezone.now()
    shop.save(update_fields=[
        "approval_status", "rejection_reason", "reviewed_by", "reviewed_by_user_id", "reviewed_at", "updated_at",
    ])
    return shop


def review_shop(shop_id, decision, reason="", identity_confirmed=False, *, reviewer_user_id):
    """Apply a /console decision. The row is locked so two admins deciding at
    once are checked one after the other. Raises Shop.DoesNotExist or
    ShopReviewError."""
    with transaction.atomic():
        shop = Shop.objects.select_for_update().get(pk=shop_id)
        errors = review_errors(shop, decision, reason, identity_confirmed)
        if errors:
            raise ShopReviewError(errors)
        return record_review(shop, decision, reason, reviewer_user_id=reviewer_user_id)
