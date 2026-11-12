from django import forms
from django.contrib import admin

from .models import Allocation, Shop
from .shop_review import record_review, review_errors

REVIEW_PERMISSION = "marketplace.review_shop"


class ShopReviewForm(forms.ModelForm):
    """The decision a team admin records on a shop's admin page."""

    identity_confirmed = forms.BooleanField(
        required=False,
        label="I confirmed the applicant represents this shop",
        help_text="Required to approve. This is a manual onboarding check, not a food-safety or legal certification.",
    )

    class Meta:
        model = Shop
        fields = ["approval_status", "rejection_reason"]
        help_texts = {"rejection_reason": "Required to reject. The shop owner sees this text in their workspace."}

    def clean(self):
        cleaned = super().clean()
        status = cleaned.get("approval_status")
        if status is None:
            return cleaned

        # LP-57: the same rules as /console, from one shared function.
        errors = review_errors(
            self.instance, status, cleaned.get("rejection_reason", ""), cleaned.get("identity_confirmed")
        )
        for field, message in errors.items():
            self.add_error(field, message)
        if status == Shop.ApprovalStatus.APPROVED:
            cleaned["rejection_reason"] = ""
        return cleaned


@admin.register(Shop)
class ShopAdmin(admin.ModelAdmin):
    """Review only. Shops are created through registration and edited by their
    owners, so only the decision is editable here, and shops are never deleted."""

    form = ShopReviewForm
    list_display = ["name", "area", "approval_status", "profile_is_complete", "reviewed_by", "reviewed_at", "updated_at"]
    list_filter = ["approval_status"]
    search_fields = ["name", "area"]
    ordering = ["created_at"]
    readonly_fields = [
        "name", "operator_user_id", "address", "area", "public_contact", "profile_is_complete",
        "reviewed_by", "reviewed_by_user_id", "reviewed_at", "created_at", "updated_at",
    ]

    @admin.display(boolean=True, description="Profile complete")
    def profile_is_complete(self, shop):
        return shop.profile_complete

    def get_fieldsets(self, request, obj=None):
        decision = ["approval_status", "rejection_reason"]
        if self.has_change_permission(request, obj):
            decision.append("identity_confirmed")
        return [
            ("Shop details", {"fields": ["name", "operator_user_id", "address", "area", "public_contact", "profile_is_complete"]}),
            ("Decision", {"fields": decision}),
            ("Review record", {"fields": ["reviewed_by", "reviewed_by_user_id", "reviewed_at", "created_at", "updated_at"]}),
        ]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm(REVIEW_PERMISSION)

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm(REVIEW_PERMISSION) or super().has_view_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if not {"approval_status", "rejection_reason"} & set(form.changed_data):
            return
        record_review(obj, obj.approval_status, obj.rejection_reason, reviewer=request.user)


# LP-20: team admins can see stock held, sold and returned for reservations.
# Read-only: allocations change only through the internal stock API.
@admin.register(Allocation)
class AllocationAdmin(admin.ModelAdmin):
    list_display = ("reservation_ref", "listing", "quantity", "status", "reject_reason", "created_at", "finished_at")
    list_filter = ("status", "reject_reason")
    search_fields = ("=reservation_ref", "=listing__id", "listing__title")
    list_select_related = ("listing",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
