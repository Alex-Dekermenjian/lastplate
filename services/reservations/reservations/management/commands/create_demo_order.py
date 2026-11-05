"""LP-28 development helper: create a PENDING order so the restaurant's
Accept / Reject flow can be tried before checkout (LP-19) exists.

    python manage.py create_demo_order --customer-id 12 --customer-name "Rami" --line 5:2 --line 6:1

Each --line is LISTING_ID:QUANTITY; all lines must be from one restaurant.
Stock is held in Marketplace exactly as a real order would hold it. This is
not the checkout: LP-19 replaces it with the customer's own order endpoint,
which must save the order before contacting Marketplace.
"""

import uuid
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from reservations import marketplace
from reservations.models import Order, OrderLine


def parse_line(text):
    try:
        listing_id, quantity = (int(part) for part in text.split(":"))
    except ValueError:
        raise CommandError(f"--line {text!r}: use LISTING_ID:QUANTITY, for example 5:2.")
    if listing_id <= 0 or quantity <= 0:
        raise CommandError(f"--line {text!r}: both numbers must be positive.")
    return listing_id, quantity


class Command(BaseCommand):
    help = "Create a demo PENDING order (development only, until LP-19)."

    def add_arguments(self, parser):
        parser.add_argument("--customer-id", type=int, required=True)
        parser.add_argument("--customer-name", default="Demo customer (fictional)")
        parser.add_argument("--line", action="append", required=True, type=parse_line)

    def handle(self, *args, **options):
        held = []
        try:
            for listing_id, quantity in options["line"]:
                ref = uuid.uuid4()
                status, body = marketplace.allocate(ref, listing_id, quantity)
                if status not in (200, 201) or body.get("status") != "HELD":
                    raise CommandError(f"Listing {listing_id} could not be held: "
                                       f"{body.get('reject_reason') or body.get('detail') or status}.")
                held.append((ref, body))
            if len({body["shop_id"] for _, body in held}) != 1:
                raise CommandError("All lines must come from one restaurant.")
        except (CommandError, marketplace.MarketplaceUnavailable) as error:
            for ref, body in held:
                marketplace.release(ref, body["listing_id"])
            if isinstance(error, CommandError):
                raise
            raise CommandError("Marketplace could not be reached.")

        first = held[0][1]
        with transaction.atomic():
            order = Order.objects.create(
                customer_user_id=options["customer_id"],
                customer_name=options["customer_name"],
                shop_id=first["shop_id"],
                shop_name=first["shop_name"],
                shop_operator_user_id=first["shop_operator_user_id"],
            )
            for ref, body in held:
                OrderLine.objects.create(
                    order=order,
                    listing_id=body["listing_id"],
                    allocation_ref=ref,
                    title=body["title"],
                    unit_price=Decimal(body["unit_price"]),
                    quantity=body["quantity"],
                    pickup_start=body["pickup_start"],
                    pickup_end=body["pickup_end"],
                )
        self.stdout.write(self.style.SUCCESS(f"Created order {order.ref} for {first['shop_name']}."))
