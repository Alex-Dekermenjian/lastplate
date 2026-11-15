"""LP-25: whenever the offers list is shown, the web first asks Reservations
to expire late orders, so their food is back on sale even if the background
timer is asleep. It never makes a page fail."""

from unittest.mock import call, patch

from django.test import SimpleTestCase

from . import services
from .services import ServiceUnavailable

EXPIRE = call("reservations", "POST", "/api/timers/expire", {}, timeout=services.EXPIRE_TIMEOUT_SECONDS)


class OffersListExpiresLateOrdersTests(SimpleTestCase):
    def test_every_offers_read_expires_late_orders_first(self):
        for read, path in ((services.browse_listings, "/api/listings"),
                           (lambda: services.listing_offer(5), "/api/listings/5"),
                           (lambda: services.restaurant_page(3), "/api/shops/3")):
            with patch.object(services, "_call", return_value=(200, {})) as api:
                self.assertEqual(read(), (200, {}))
            self.assertEqual(api.call_args_list, [EXPIRE, call("marketplace", "GET", path)])

    def test_reservations_down_never_breaks_the_offers_list(self):
        with patch.object(services, "_call", side_effect=[ServiceUnavailable("reservations"), (200, [])]):
            self.assertEqual(services.browse_listings(), (200, []))
