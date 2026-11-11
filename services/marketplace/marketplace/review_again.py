"""LP-45: a rejected restaurant fixes its details and asks for review again.

Only the owner's own Rejected restaurant can ask, and asking only ever sets
it back to Pending: an owner can never approve their own restaurant. The
admin's earlier reason is kept for the next review, with the owner's note.
"""

from django.db import transaction
from django.utils import timezone

from .models import Shop

NOT_REJECTED = "Only a restaurant that was not approved can ask for review again."


class ReviewAgainError(Exception):
    def __init__(self, code, detail, http_status):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.http_status = http_status


def request_review_again(operator_user_id, note=""):
    with transaction.atomic():
        shop = Shop.objects.select_for_update().filter(operator_user_id=operator_user_id).first()
        if shop is None:
            raise ReviewAgainError("no_shop", "You have no shop yet.", 404)
        if shop.approval_status != Shop.ApprovalStatus.REJECTED:
            raise ReviewAgainError("not_rejected", NOT_REJECTED, 409)
        shop.last_rejection_reason = shop.rejection_reason
        shop.rejection_reason = ""
        shop.approval_status = Shop.ApprovalStatus.PENDING
        shop.review_requests += 1
        shop.review_request_note = (note or "").strip()
        shop.review_requested_at = timezone.now()
        shop.save(update_fields=[
            "approval_status", "rejection_reason", "last_rejection_reason", "review_requests",
            "review_request_note", "review_requested_at", "updated_at",
        ])
    return shop
