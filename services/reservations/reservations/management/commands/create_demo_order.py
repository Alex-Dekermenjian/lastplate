"""LP-28 development helper: create a Placed order so the restaurant's
Accept / Reject flow can be tried before checkout (LP-19) exists.

    python manage.py create_demo_order --customer-id 12 --customer-name "Rami" --line 5:2 --line 6:1

Each --line is LISTING_ID:QUANTITY; all lines must be from one restaurant.
Stock for all lines is held together in Marketplace, as LP-19 requires: if one
line is short, nothing is held. This is not the checkout: LP-19 replaces it
with the customer's order endpoint, which must also save the order before
contacting Marketplace (docs/microservices-plan.md section 6).
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
    help = "Create a demo Placed order (development only, until LP-19)."

    def add_arguments(self, parser):
        parser.add_argument("--customer-id", type=int, required=True)
        parser.add_argument("--customer-name", default="Demo customer (fictional)")
        parser.add_argument("--line", action="append", required=True, type=parse_line)

    def handle(self, *args, **options):
        lines = [
            {"reservation_ref": str(uuid.uuid4()), "listing_id": listing_id, "quantity": quantity}
            for listing_id, quantity in options["line"]
        ]
        try:
            status, body = marketplace.allocate(lines)
        except marketplace.MarketplaceUnavailable:
            raise CommandError("Marketplace could not be reached; nothing was ordered.")
        if status not in (200, 201) or body.get("status") != "HELD":
            reasons = ", ".join(
                f"listing {line['listing_id']}: {line['reject_reason']}"
                for line in body.get("lines", []) if line.get("reject_reason")
            )
            raise CommandError(f"Nothing was held ({reasons or body.get('detail') or status}).")

        shop = body["shop"]
        held = body["lines"]
        with transaction.atomic():
            order = Order.objects.create(
                customer_user_id=options["customer_id"],
                customer_name=options["customer_name"],
                request_key=f"demo-{uuid.uuid4()}",
                shop_id=shop["id"],
                shop_name=shop["name"],
                shop_address=shop["address"],
                shop_operator_user_id=shop["operator_user_id"],
                total=sum((Decimal(line["unit_price"]) * line["quantity"] for line in held), Decimal("0")),
            )
            for line in held:
                OrderLine.objects.create(
                    order=order,
                    listing_id=line["listing_id"],
                    allocation_ref=line["reservation_ref"],
                    title=line["title"],
                    unit_price=Decimal(line["unit_price"]),
                    quantity=line["quantity"],
                    pickup_start=line["pickup_start"],
                    pickup_end=line["pickup_end"],
                )
        self.stdout.write(self.style.SUCCESS(
            f"Created order {order.ref} for {shop['name']}: ${order.total}, pickup code {order.pickup_code}."
        ))
