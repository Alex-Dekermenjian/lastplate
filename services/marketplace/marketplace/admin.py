from django import forms
from django.contrib import admin
from django.utils import timezone

from .models import Shop

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
        reason = cleaned.get("rejection_reason", "")
        Status = Shop.ApprovalStatus

        if status == Status.APPROVED:
            if self.instance.approval_status != Status.APPROVED:
                if not self.instance.profile_complete:
                    self.add_error("approval_status", "This shop's profile is incomplete, so it cannot be approved.")
                if not cleaned.get("identity_confirmed"):
                    self.add_error("identity_confirmed", "Confirm the applicant represents this shop before approving.")
            cleaned["rejection_reason"] = ""
        elif status == Status.REJECTED:
            if not reason:
                self.add_error("rejection_reason", "Give a reason. The shop owner will see it.")
        elif status == Status.PENDING:
            if self.instance.approval_status != Status.PENDING:
                self.add_error("approval_status", "A reviewed shop cannot return to Pending. Approve or reject it.")
            elif reason:
                self.add_error("rejection_reason", "Choose Rejected to give a reason.")
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
        "reviewed_by", "reviewed_at", "created_at", "updated_at",
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
            ("Review record", {"fields": ["reviewed_by", "reviewed_at", "created_at", "updated_at"]}),
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
        obj.reviewed_by = request.user
        obj.reviewed_at = timezone.now()
        # Save only the decision, so a profile edit made at the same moment survives.
        obj.save(update_fields=["approval_status", "rejection_reason", "reviewed_by", "reviewed_at", "updated_at"])
