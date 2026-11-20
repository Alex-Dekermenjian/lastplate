"""Demo clock only: orders from "the future" are deleted when the clock moves back.

Orders are never deleted in LastPlate, with this one exception: when someone
moves the demo clock back, an order created after the new time would be an
order from the future, so it is removed completely (with its lines, history
and any strike removal) and Marketplace puts its food back on sale. It never
reappears when the clock passes that time again.

Marketplace is asked first. If it cannot be reached nothing is deleted, so the
stock and the orders never disagree.
"""

from django.db import connection, models, transaction

from . import marketplace_client
from .models import Notification, Order, OrderEvent, OrderLine, StrikeRemoval


# LP-17's database triggers refuse deletes on these tables (migration 0002).
TRIGGERS = (
    ("reservations_order", "reservations_order_no_delete"),
    ("reservations_orderline", "reservations_orderline_no_delete"),
    ("reservations_orderevent", "reservations_orderevent_no_delete"),
)


def _switch_triggers(action):
    """ENABLE or DISABLE the no-delete triggers. Inside the surrounding
    transaction, so a failure puts them back exactly as they were."""
    with connection.cursor() as cursor:
        # PostgreSQL will not ALTER a table that still has deferred foreign-key
        # checks waiting, so run them now.
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        for table, trigger in TRIGGERS:
            cursor.execute(f"ALTER TABLE {table} {action} TRIGGER {trigger}")


def _remove(queryset):
    # The models refuse delete() on purpose; this is the one deliberate bypass.
    models.QuerySet.delete(queryset)


def rewind(since):
    """Delete every order created after `since`. Returns (orders, allocations)
    removed. Raises marketplace_client.MarketplaceUnavailable, deleting nothing."""
    rows = list(Order.objects.filter(created_at__gt=since).values_list("id", "reservation_ref"))
    ids = [pk for pk, _ref in rows]
    allocations = marketplace_client.demo_rewind(since, [ref for _pk, ref in rows])
    with transaction.atomic():
        _switch_triggers("DISABLE")
        _remove(Notification.objects.filter(order_id__in=ids))
        _remove(StrikeRemoval.objects.filter(order_id__in=ids))
        _remove(OrderEvent.objects.filter(order_id__in=ids))
        _remove(OrderLine.objects.filter(order_id__in=ids))
        _remove(Order.objects.filter(id__in=ids))
        _switch_triggers("ENABLE")
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL DEFERRED")
    return len(ids), allocations
