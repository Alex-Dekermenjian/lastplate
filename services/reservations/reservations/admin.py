"""LP-17: team admins can look at orders, their items and history.

The admin is read-only. Orders are created by checkout, change status only
through reservations.orders.change_status, and are never deleted.
"""

from django.contrib import admin

from .models import Order, OrderEvent, OrderLine
from .orders import pickup_window_text, time_text


class ReadOnlyInline(admin.TabularInline):
    extra = 0
    can_delete = False
    show_change_link = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class OrderLineInline(ReadOnlyInline):
    model = OrderLine
    fields = readonly_fields = ("listing_id", "title", "quantity", "unit_price", "pickup_window")
    verbose_name = "item (copy taken when the order was placed)"
    verbose_name_plural = "items (copies taken when the order was placed)"

    @admin.display(description="Pickup window (Beirut time)")
    def pickup_window(self, line):
        return pickup_window_text(line.pickup_start, line.pickup_end)


class OrderEventInline(ReadOnlyInline):
    model = OrderEvent
    fields = readonly_fields = ("when", "from_status", "to_status", "actor", "reason")
    verbose_name = "history row"
    verbose_name_plural = "history"

    @admin.display(description="When (Beirut time)")
    def when(self, event):
        return time_text(event.created_at)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ("pickup_code", "status_shown", "customer_user_id", "shop_id", "branch_id",
                    "total_cash", "placed", "pick_up_by_text")
    list_filter = ("status", "sync_state", "cancelled_by")
    search_fields = ("pickup_code", "=reservation_ref", "=customer_user_id", "=shop_id")
    inlines = (OrderLineInline, OrderEventInline)
    fieldsets = (
        (None, {"fields": ("pickup_code", "status_shown", "status", "sync_state", "total_cash")}),
        ("Who", {"fields": ("customer_user_id", "shop_id", "branch_id")}),
        ("Times (Beirut time)", {"fields": ("placed", "pick_up_by_text", "collected_text")}),
        ("Cancellation", {"fields": ("cancelled_by", "cancel_reason")}),
        ("Reference", {"fields": ("reservation_ref", "request_key")}),
    )
    readonly_fields = (
        "pickup_code", "status_shown", "status", "sync_state", "total_cash",
        "customer_user_id", "shop_id", "branch_id",
        "placed", "pick_up_by_text", "collected_text",
        "cancelled_by", "cancel_reason", "reservation_ref", "request_key",
    )

    @admin.display(description="Customers and restaurants see")
    def status_shown(self, order):
        return order.status_label or "(not shown as an order)"

    @admin.display(description="Placed", ordering="created_at")
    def placed(self, order):
        return time_text(order.created_at)

    @admin.display(description="Pick up by", ordering="pick_up_by")
    def pick_up_by_text(self, order):
        return time_text(order.pick_up_by)

    @admin.display(description="Picked up and paid")
    def collected_text(self, order):
        return time_text(order.collected_at)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_actions(self, request):
        actions = super().get_actions(request)
        actions.pop("delete_selected", None)
        return actions
