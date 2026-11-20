"""LP-30: "same second" tests in Marketplace, on the real PostgreSQL test
database (TransactionTestCase), one thread per action, each thread with its
own database connection.

- checkout x the offer being taken down (LP-29 withdraw_listing): the reserve
  call and the withdrawal take the same listing lock, so either the hold got
  in first (and stays valid: it can still be picked up and sold), or the
  reserve is refused as not_published. Never both, never half.
- release x consume of the same hold (an expiry racing a Paid order's
  consumption): only one leaves HELD, so stock changes exactly once.
- 20 people reserving the last item is LP-20's
  test_twenty_people_one_last_item_exactly_one_gets_it.
"""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from django.db import close_old_connections
from django.test import TransactionTestCase

from .allocation_service import AllocationError, consume, release, reserve
from .listing_service import withdraw_listing
from .models import Allocation, Listing
from .stock import free_quantity
from .test_browse import make_listing, make_shop

ROUNDS = 5


def race(*actions):
    barrier = threading.Barrier(len(actions))

    def run(action):
        close_old_connections()
        try:
            barrier.wait(timeout=10)
            return action()
        except AllocationError as error:
            return error
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=len(actions)) as pool:
        return list(pool.map(run, actions))


class CheckoutVersusTakenDownTests(TransactionTestCase):
    def test_reserve_and_withdraw_at_the_same_moment(self):
        shop = make_shop()
        for _ in range(ROUNDS):
            listing = make_listing(shop, total_quantity=3)
            ref = uuid.uuid4()
            (allocation, _created), withdrawn = race(
                lambda: reserve(ref, listing.pk, 1, listing.terms_revision),
                lambda: withdraw_listing(listing.pk, shop.pk))
            listing.refresh_from_db()
            self.assertEqual(listing.status, Listing.Status.WITHDRAWN)
            self.assertEqual(Allocation.objects.filter(reservation_ref=ref).count(), 1)
            if allocation.status == Allocation.Status.HELD:
                # Got in first: still valid, and the sale goes through.
                self.assertEqual(free_quantity(listing), 2)
                consume(ref, listing.pk)
                self.assertEqual(Allocation.objects.get(reservation_ref=ref).status, Allocation.Status.CONSUMED)
            else:
                self.assertEqual((allocation.status, allocation.reject_reason),
                                 (Allocation.Status.REJECTED, Allocation.RejectReason.NOT_PUBLISHED))
                self.assertEqual(free_quantity(listing), 3)


class ReleaseVersusConsumeTests(TransactionTestCase):
    def test_release_and_consume_of_one_hold_change_stock_once(self):
        shop = make_shop()
        listing = make_listing(shop, total_quantity=10)
        for _ in range(ROUNDS):
            ref = uuid.uuid4()
            reserve(ref, listing.pk, 2, listing.terms_revision)
            results = race(lambda: release(ref, listing.pk), lambda: consume(ref, listing.pk))
            winners = [r for r in results if not isinstance(r, AllocationError) and r[1]]
            self.assertEqual(len(winners), 1, results)
            final = Allocation.objects.get(reservation_ref=ref).status
            self.assertIn(final, (Allocation.Status.RELEASED, Allocation.Status.CONSUMED))
        listing.refresh_from_db()
        sold = Allocation.objects.filter(listing=listing, status=Allocation.Status.CONSUMED).count()
        self.assertEqual(free_quantity(listing), 10 - 2 * sold)
