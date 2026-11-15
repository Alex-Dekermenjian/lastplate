"""LP-29: when a restaurant takes an offer down (Marketplace: WITHDRAWN).

- Checkout refuses it: the recheck finds it gone (404), or, if it was taken
  down a moment after the recheck, Marketplace refuses the hold
  (not_published). Nothing is reserved either way.
- Orders already reserved stay valid: the restaurant can still look the code
  up and take the cash. Pickup works from this service's own order records
  and never asks Marketplace whether the offer is still listed.
"""

from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import Order, Status, SyncState
from .test_lp19_checkout import PUBLIC_PEM, CheckoutTestCase, token
from .test_lp24_collection import BASE, SHOP, reserved


class UnpublishedOfferCheckoutTests(CheckoutTestCase):

    def take_down(self, listing_id):
        self.market.offers[listing_id]["status"] = "WITHDRAWN"

    def test_checkout_refuses_an_unpublished_offer(self):
        self.take_down(41)

        response = self.reserve((41, 1), (42, 1))

        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertEqual(body["code"], "cart_changed")
        self.assertEqual([(p["listing_id"], p["code"]) for p in body["problems"]], [(41, "not_available")])
        self.assertFalse(Order.objects.exists())
        self.assertFalse([call for call in self.market.calls if call[0] == "allocate"])
        self.assertEqual(self.market.free(42), 5)

    def test_taken_down_just_after_the_recheck_is_refused_and_nothing_stays_held(self):
        allocate = self.market.allocate

        def taken_down_meanwhile(ref, listing_id, quantity, revision):
            if listing_id == 42:
                self.take_down(42)
            return allocate(ref, listing_id, quantity, revision)

        with patch("reservations.marketplace_client.allocate", side_effect=taken_down_meanwhile):
            response = self.reserve((41, 1), (42, 1))

        self.assertEqual(response.status_code, 409)
        problem = response.json()["problems"][0]
        self.assertEqual((problem["listing_id"], problem["code"], problem["detail"]),
                         (42, "taken_down", "This offer was taken down."))
        self.assertEqual(self.market.free(41), 3)                 # the donut hold was released
        self.assertEqual(Order.objects.get().status, Status.NOT_RESERVED)


@override_settings(JWT_PUBLIC_KEY=PUBLIC_PEM)
class PickupAfterUnpublishTests(TestCase):
    """The order was reserved, then the restaurant took the offer down."""

    def setUp(self):
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION="Bearer " + token(role="SHOP_OPERATOR"))
        for path, kwargs in (
            ("reservations.collection_views.operator_shop", {"return_value": (200, SHOP)}),
            ("reservations.shop_views.operator_shop", {"return_value": (200, SHOP)}),
            # Marketplace would now answer 404 for the offer; pickup must not ask.
            ("reservations.marketplace_client._request", {"side_effect": AssertionError("pickup asked Marketplace")}),
        ):
            patcher = patch(path, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.order = reserved()

    def test_unpublish_keeps_existing_orders_and_pickup_still_works(self):
        looked_up = self.client.post(BASE + "/lookup", {"branch_id": 7, "pickup_code": self.order.pickup_code},
                                     format="json")
        self.assertEqual(looked_up.status_code, 200, looked_up.content)
        self.assertEqual(looked_up.json()["lines"][0]["title"], "Bread")

        paid = self.client.post(f"{BASE}/{self.order.reservation_ref}/collect",
                                {"branch_id": 7, "version": 0, "cash_received": True}, format="json")

        self.assertEqual(paid.status_code, 200, paid.content)
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.sync_state), (Status.COLLECTED, SyncState.CONSUME_PENDING))
