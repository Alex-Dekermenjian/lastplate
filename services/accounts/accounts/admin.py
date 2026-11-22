from django.contrib import admin

from .models import TemporaryPasswordLog


@admin.register(TemporaryPasswordLog)
class TemporaryPasswordLogAdmin(admin.ModelAdmin):
    """LP-50: read-only audit of temporary passwords (never the password)."""

    list_display = ["created_at", "user_email", "set_by_email"]
    search_fields = ["user_email", "set_by_email"]
    readonly_fields = ["user", "user_email", "set_by", "set_by_email", "created_at"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
