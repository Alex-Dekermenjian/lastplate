"""LP-60: the LastPlate admin stops a bad restaurant or offer.

- suspend_shop: with a reason. The restaurant and all its offers leave the
  site (browse.py shows only approved, unsuspended shops), new reservations
  are refused (shop_suspended) and it cannot publish. Its owner sees the
  reason. Existing Reserved orders stay valid: their units stay held and can
  still be picked up and sold.
- restore_shop: brings it all back.
- remove_listing: one offer, with a reason. It becomes WITHDRAWN (as when
  the owner takes it down), so customers no longer see it and new
  reservations are refused; existing ones stay valid. The owner sees why.

Only Accounts tokens with role ADMIN reach these (admin_api.py).
"""

from django.db import transaction
from django.utils import timezone

from .models import Listing, Shop

MAX_REASON = 2000


class ModerationError(Exception):
    def __init__(self, code, detail, http_status):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.http_status = http_status


def _reason(value):
    reason = value.strip() if isinstance(value, str) else ""
    if not reason:
        raise ModerationError("reason_required", "Give a reason. The owner will see it.", 400)
    if len(reason) > MAX_REASON:
        raise ModerationError("reason_too_long", f"Keep the reason under {MAX_REASON} characters.", 400)
    return reason


def _locked_shop(shop_id):
    shop = Shop.objects.select_for_update().filter(pk=shop_id).first()
    if shop is None:
        raise ModerationError("not_found", "Shop not found.", 404)
    return shop


def suspend_shop(shop_id, reason, admin_user_id, now=None):
    reason = _reason(reason)
    with transaction.atomic():
        shop = _locked_shop(shop_id)
        if shop.approval_status != Shop.ApprovalStatus.APPROVED:
            raise ModerationError("not_approved", "Only approved restaurants can be suspended.", 409)
        if shop.suspended_at is not None:
            raise ModerationError("already_suspended", "This restaurant is already suspended.", 409)
        shop.suspended_at = now or timezone.now()
        shop.suspension_reason = reason
        shop.suspended_by_user_id = admin_user_id
        shop.save(update_fields=["suspended_at", "suspension_reason", "suspended_by_user_id", "updated_at"])
    return shop


def restore_shop(shop_id):
    with transaction.atomic():
        shop = _locked_shop(shop_id)
        if shop.suspended_at is None:
            raise ModerationError("not_suspended", "This restaurant is not suspended.", 409)
        shop.suspended_at = None
        shop.suspension_reason = ""
        shop.suspended_by_user_id = None
        shop.save(update_fields=["suspended_at", "suspension_reason", "suspended_by_user_id", "updated_at"])
    return shop


def remove_listing(listing_id, reason, admin_user_id, now=None):
    reason = _reason(reason)
    with transaction.atomic():
        # The same lock reserve calls take: a reserve after this is refused.
        listing = Listing.objects.select_for_update().filter(pk=listing_id).first()
        if listing is None or listing.status == Listing.Status.DRAFT:
            raise ModerationError("not_found", "Offer not found.", 404)
        if listing.removed_at is not None:
            raise ModerationError("already_removed", "This offer was already removed.", 409)
        if listing.status != Listing.Status.PUBLISHED:
            raise ModerationError("not_published", "Only published offers can be removed.", 409)
        listing.status = Listing.Status.WITHDRAWN
        listing.removed_at = now or timezone.now()
        listing.removal_reason = reason
        listing.removed_by_user_id = admin_user_id
        listing.save(update_fields=["status", "removed_at", "removal_reason", "removed_by_user_id", "updated_at"])
    return listing
