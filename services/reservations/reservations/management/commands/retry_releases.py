"""LP-28: finish stock releases for rejected orders whose first release call
didn't reach Marketplace. Safe to run any time, as often as needed: a release
frees the units only once."""

from django.core.management.base import BaseCommand

from reservations.order_service import retry_pending_releases


class Command(BaseCommand):
    help = "Retry Marketplace stock releases for rejected orders."

    def handle(self, *args, **options):
        orders = retry_pending_releases()
        done = sum(order.sync_state == "SYNCED" for order in orders)
        self.stdout.write(f"{done} of {len(orders)} pending release(s) confirmed.")
