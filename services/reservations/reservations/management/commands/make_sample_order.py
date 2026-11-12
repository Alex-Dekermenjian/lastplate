"""LP-17: add one clearly fictional order, to look at it in the admin.

Development only (refused unless DJANGO_DEBUG is on). Checkout does not exist
yet (LP-19), so this is the way to see an order before then. Example:

    python manage.py make_sample_order --ordered-at 18:15
    python manage.py make_sample_order --ordered-at 20:30 --then cancel
"""

import uuid
from datetime import datetime, time
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from reservations.models import Actor, Status, SyncState
from reservations.orders import BEIRUT, change_status, create_order, pickup_window_text, time_text

THEN = {
    "cancel": (Status.CANCELLED, Actor.CUSTOMER, "Sample: customer changed their mind", SyncState.RELEASE_PENDING),
    "shop-cancel": (Status.CANCELLED, Actor.SHOP, "Sample: the donuts ran out", SyncState.SHOP_CANCEL_PENDING),
    "collect": (Status.COLLECTED, Actor.SHOP, "", SyncState.CONSUME_PENDING),
    "expire": (Status.EXPIRED, Actor.SYSTEM, "Sample: not picked up in time", SyncState.RELEASE_PENDING),
}


def _clock(value):
    try:
        return time.fromisoformat(value)
    except ValueError as error:
        raise CommandError(f"Use a 24-hour time such as 18:15, not {value!r}.") from error


class Command(BaseCommand):
    help = "Development only: add one fictional order (donuts, 18:00-21:00 Beirut time today)."

    def add_arguments(self, parser):
        parser.add_argument("--ordered-at", default="18:15", help="Beirut time today the order was placed (default 18:15).")
        parser.add_argument("--window", default="18:00-21:00", help="Pickup window today, Beirut time (default 18:00-21:00).")
        parser.add_argument("--then", choices=sorted(THEN), help="After it is Reserved, also cancel, collect or expire it.")

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("make_sample_order is for local development only (DJANGO_DEBUG must be True).")

        today = timezone.localtime(timezone.now(), BEIRUT).date()

        def at(clock):
            return datetime.combine(today, clock, tzinfo=BEIRUT)

        try:
            start_text, end_text = options["window"].split("-")
        except ValueError as error:
            raise CommandError("Write the window as 18:00-21:00.") from error
        start, end, ordered = at(_clock(start_text)), at(_clock(end_text)), at(_clock(options["ordered_at"]))
        if end <= start:
            raise CommandError("The window must end after it starts.")

        order = create_order(
            customer_user_id=999001, shop_id=999001, branch_id=999001,
            request_key=f"sample-{uuid.uuid4()}", now=ordered,
            lines=[{
                "listing_id": 999001, "quantity": 2, "title": "SAMPLE (fictional) glazed donuts",
                "unit_price": Decimal("1.50"), "pickup_start": start, "pickup_end": end,
            }],
        )
        # Stands in for Marketplace's "held" answer, which arrives with LP-19/LP-20.
        order = change_status(order, Status.CONFIRMED, actor=Actor.SYSTEM, sync_state=SyncState.SYNCED,
                              reason="Sample: stock held", now=ordered)
        if options["then"]:
            to_status, actor, reason, sync_state = THEN[options["then"]]
            order = change_status(order, to_status, actor=actor, reason=reason, sync_state=sync_state,
                                  now=max(timezone.now(), ordered))

        self.stdout.write(self.style.SUCCESS(f"Order {order.pickup_code} (id {order.pk}): {order.status_label}"))
        self.stdout.write(f"  Pickup window: {pickup_window_text(start, end)}")
        self.stdout.write(f"  Placed:        {time_text(order.created_at)}")
        self.stdout.write(f"  Pick up by:    {time_text(order.pick_up_by)}  (hold {settings.PICKUP_HOLD_MINUTES} min)")
        self.stdout.write(f"  Total cash:    ${order.total_cash}")
