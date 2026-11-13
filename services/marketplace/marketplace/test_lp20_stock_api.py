"""LP-20: the internal stock API (reserve, release, mark sold).

One test class per "Done when" line of the Jira card, plus its "Tests to
write" list. The race test runs on the real PostgreSQL test database, in CI
too, with 20 threads that each use their own database connection.
"""

import re
import threading
import uuid
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.core.exceptions import ImproperlyConfigured
from django.db import connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from config.settings import service_keys

from . import service_auth
from .allocation_service import AllocationError, consume, release, reserve, shop_cancel
from .authentication import TokenUser
from .browse import visible_listing, visible_listings
from .models import Allocation, Listing, Shop, StockAdjustment
from .stock import committed_quantity, free_quantity, with_committed
from .stock_service import StockAdjustmentError, adjust_stock
from .test_browse import OPERATOR_ID, make_listing, make_shop

# Test-only keys, never used anywhere else.
KEY = "test-only-reservations-key-" + "r" * 24
OLD_KEY = "test-only-previous-reservations-key-" + "p" * 16
WEB_KEY = "test-only-web-key-" + "w" * 32
TEST_KEYS = {"reservations": [KEY, OLD_KEY], "web": [WEB_KEY]}
SERVICE_DIR = Path(__file__).resolve().parent.parent
RESERVATIONS_DIR = SERVICE_DIR.parent / "reservations"


def service_client(name="reservations", key=KEY):
    client = APIClient()
    headers = {}
    if name is not None:
        headers["HTTP_X_SERVICE_NAME"] = name
    if key is not None:
        headers["HTTP_X_SERVICE_KEY"] = key
    client.credentials(**headers)
    return client


def reserve_body(listing, quantity=1, ref=None):
    return {
        "reservation_ref": str(ref or uuid.uuid4()),
        "listing_id": listing.pk,
        "quantity": quantity,
        "expected_terms_revision": listing.terms_revision,
    }


@override_settings(INTERNAL_SERVICE_KEYS=TEST_KEYS)
class StockApiTestCase(TestCase):
    def setUp(self):
        self.shop = make_shop()
        # The card's example: 3 donuts.
        self.listing = make_listing(self.shop, title="Glazed donuts", total_quantity=3, discounted_price="1.50")
        self.api = service_client()

    def post_reserve(self, quantity=1, ref=None, listing=None):
        return self.api.post(reverse("internal-allocations"),
                             reserve_body(listing or self.listing, quantity, ref), format="json")

    def post_action(self, action, ref, listing=None, **extra):
        url = reverse(f"internal-allocation-{action}", kwargs={"ref": ref})
        return self.api.post(url, {"listing_id": (listing or self.listing).pk, **extra}, format="json")

    def customers_see(self, listing=None):
        """free_quantity on the public offer page, or None if it is not listed."""
        response = APIClient().get(reverse("listing_detail", kwargs={"pk": (listing or self.listing).pk}))
        return response.json()["free_quantity"] if response.status_code == 200 else None

    def free(self):
        return free_quantity(Listing.objects.get(pk=self.listing.pk))


class MarketplaceOnlyChangesStockTests(StockApiTestCase):
    """Done when: Marketplace is the only service that changes stock;
    Reservations asks through an API call and never touches its database."""

    def test_reservations_changes_stock_only_through_the_internal_api(self):
        self.assertEqual(self.customers_see(), 3)
        response = self.post_reserve(quantity=1)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.customers_see(), 2)
        self.assertEqual(Allocation.objects.get().status, Allocation.Status.HELD)

    def test_reservations_code_never_reaches_the_marketplace_database(self):
        settings_text = (RESERVATIONS_DIR / "config" / "settings.py").read_text(encoding="utf-8")
        self.assertEqual(settings_text.count('"ENGINE"'), 1, "Reservations has exactly one database")
        # Importing Marketplace's code (its models) would mean reading its
        # database; a module named after it, like marketplace_client, is fine.
        imports_marketplace = re.compile(r"^\s*(from\s+marketplace[\s.]|import\s+marketplace\b)", re.M)
        for path in RESERVATIONS_DIR.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("lastplate_marketplace", text, path)
            self.assertIsNone(imports_marketplace.search(text), path)

    def test_stock_endpoints_are_internal_only(self):
        # The public API has no way to hold or release stock.
        for name in ("internal-allocations",):
            self.assertTrue(reverse(name).startswith("/internal/"))
        self.assertEqual(APIClient().post(reverse("internal-allocations"), reserve_body(self.listing),
                                          format="json").status_code, 401)
        self.assertFalse(Allocation.objects.exists())


class ServiceKeyTests(StockApiTestCase):
    """Done when: calls are accepted only from Reservations, with a secret
    key kept in private settings; a call without the key is refused."""

    def assert_refused(self, client, expected_status, code):
        response = client.post(reverse("internal-allocations"), reserve_body(self.listing), format="json")
        self.assertEqual(response.status_code, expected_status)
        self.assertEqual(response.json()["code"], code)
        self.assertFalse(Allocation.objects.exists())
        self.assertEqual(self.free(), 3)
        return response

    def test_missing_headers_are_refused_with_401(self):
        self.assert_refused(service_client(name=None, key=None), 401, "service_auth_required")
        self.assert_refused(service_client(key=None), 401, "service_auth_required")
        self.assert_refused(service_client(name=None), 401, "service_auth_required")
        response = self.assert_refused(service_client(key=""), 401, "service_auth_required")
        self.assertEqual(response["WWW-Authenticate"], "X-Service-Key")

    def test_wrong_key_or_unknown_service_is_refused_with_403(self):
        self.assert_refused(service_client(key=KEY[:-1] + "x"), 403, "invalid_service_key")
        self.assert_refused(service_client(name="accounts", key=KEY), 403, "invalid_service_key")
        # Another caller's key is not the Reservations key.
        self.assert_refused(service_client(name="reservations", key=WEB_KEY), 403, "invalid_service_key")

    def test_a_valid_key_of_another_service_is_not_allowed_here(self):
        self.assert_refused(service_client(name="web", key=WEB_KEY), 403, "caller_not_allowed")

    def test_every_internal_endpoint_needs_the_key(self):
        ref = uuid.uuid4()
        calls = [
            ("get", reverse("internal-allocation", kwargs={"ref": ref})),
            ("post", reverse("internal-allocation-release", kwargs={"ref": ref})),
            ("post", reverse("internal-allocation-consume", kwargs={"ref": ref})),
            ("post", reverse("internal-allocation-shop-cancel", kwargs={"ref": ref})),
        ]
        for method, url in calls:
            for client, expected in ((service_client(key=None), 401), (service_client(key="wrong" * 10), 403)):
                response = getattr(client, method)(url, {"listing_id": self.listing.pk}, format="json")
                self.assertEqual(response.status_code, expected, url)
        self.assertFalse(Allocation.objects.exists())

    def test_user_tokens_are_ignored_on_internal_endpoints(self):
        client = APIClient()
        client.force_authenticate(TokenUser(id=OPERATOR_ID, role="SHOP_OPERATOR", is_staff=True))
        self.assert_refused(client, 401, "service_auth_required")

    def test_service_keys_are_ignored_on_public_endpoints(self):
        self.assertIn(self.api.get(reverse("my_listings")).status_code, (401, 403))

    def test_the_previous_key_still_works_during_a_changeover(self):
        self.assertEqual(service_client(key=OLD_KEY).post(
            reverse("internal-allocations"), reserve_body(self.listing), format="json").status_code, 201)

    @override_settings(INTERNAL_SERVICE_KEYS={"reservations": []})
    def test_no_key_configured_refuses_everything(self):
        with self.assertLogs("marketplace.internal", level="ERROR"):
            self.assert_refused(service_client(), 403, "invalid_service_key")

    def test_keys_are_compared_in_constant_time(self):
        with mock.patch.object(service_auth.hmac, "compare_digest", wraps=service_auth.hmac.compare_digest) as compare:
            self.post_reserve()
        self.assertTrue(compare.called)

    def test_the_key_is_never_logged_or_returned(self):
        wrong = "wrong-key-that-must-not-be-logged-" + "z" * 20
        with self.assertLogs("marketplace.internal", level="WARNING") as logs:
            response = service_client(key=wrong).post(reverse("internal-allocations"),
                                                       reserve_body(self.listing), format="json")
        output = "\n".join(logs.output) + response.content.decode()
        for secret in (wrong, KEY, OLD_KEY):
            self.assertNotIn(secret, output)

    def test_keys_come_only_from_environment_variables(self):
        with mock.patch.dict("os.environ", {"LP20_TEST_KEY": KEY, "LP20_TEST_OLD": ""}, clear=False):
            self.assertEqual(service_keys("LP20_TEST_KEY", "LP20_TEST_OLD"), [KEY])
        with mock.patch.dict("os.environ", {"LP20_TEST_KEY": "too-short"}, clear=False):
            with self.assertRaises(ImproperlyConfigured):
                service_keys("LP20_TEST_KEY")
        settings_text = (SERVICE_DIR / "config" / "settings.py").read_text(encoding="utf-8")
        self.assertIn('service_keys("RESERVATIONS_SERVICE_KEY", "RESERVATIONS_SERVICE_KEY_PREVIOUS")', settings_text)

    def test_private_settings_files_are_ignored_by_git(self):
        ignored = (SERVICE_DIR.parent.parent / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("/compose.env", ignored)
        self.assertIn(".env", ignored)
        example = (SERVICE_DIR / ".env.example").read_text(encoding="utf-8")
        self.assertIn("RESERVATIONS_SERVICE_KEY=\n", example)  # placeholder only, no value


class HoldReleaseSellTests(StockApiTestCase):
    """Done when: reserve holds N (3 donuts -> customers see 2); release puts
    them back (2 -> 3) only while the window is open, otherwise they just count
    as unsold; mark sold makes the hold final."""

    def test_reserving_one_of_three_donuts_leaves_two_for_customers(self):
        response = self.post_reserve(quantity=1)
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual((body["status"], body["quantity"]), ("HELD", 1))
        self.assertEqual(body["terms"]["title"], "Glazed donuts")
        self.assertEqual(body["terms"]["unit_price"], "1.50")
        self.assertEqual(body["terms"]["terms_revision"], self.listing.terms_revision)
        self.assertEqual(self.customers_see(), 2)

    def test_release_while_open_puts_them_back_on_sale(self):
        ref = self.post_reserve(quantity=1).json()["reservation_ref"]
        response = self.post_action("release", ref)
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.json()["status"], response.json()["changed"]), ("RELEASED", True))
        self.assertEqual(self.customers_see(), 3)

    def test_release_after_the_window_closed_only_counts_as_unsold(self):
        ref = self.post_reserve(quantity=1).json()["reservation_ref"]
        Listing.objects.filter(pk=self.listing.pk).update(pickup_end=timezone.now() - timedelta(minutes=1))
        response = self.post_action("release", ref)
        self.assertEqual(response.status_code, 200)
        # The units are no longer held, but the ended offer is not listed, so
        # nothing goes back on sale.
        self.assertEqual(self.free(), 3)
        self.assertIsNone(self.customers_see())
        self.assertNotIn(self.listing.pk, [listing.pk for listing, _ in visible_listings()])
        self.assertEqual(self.post_reserve(quantity=1).json()["code"], "pickup_ended")

    def test_mark_sold_makes_the_hold_final(self):
        ref = self.post_reserve(quantity=2).json()["reservation_ref"]
        response = self.post_action("consume", ref)
        self.assertEqual((response.status_code, response.json()["status"]), (200, "CONSUMED"))
        self.assertEqual(self.customers_see(), 1)
        # Sold units never come back.
        refused = self.post_action("release", ref)
        self.assertEqual((refused.status_code, refused.json()["code"]), (409, "not_held"))
        self.assertEqual(self.customers_see(), 1)

    def test_restaurant_cancellation_takes_the_units_out_of_stock(self):
        ref = self.post_reserve(quantity=1).json()["reservation_ref"]
        response = self.post_action("shop-cancel", ref, actor_user_id=OPERATOR_ID, reason="Dropped the tray")
        self.assertEqual((response.status_code, response.json()["status"]), (200, "SHOP_CANCELLED"))
        listing = Listing.objects.get(pk=self.listing.pk)
        # T 3 -> 2, held 1 -> 0, so free stays 2 (database-design.md §9).
        self.assertEqual((listing.total_quantity, free_quantity(listing)), (2, 2))
        adjustment = StockAdjustment.objects.get()
        self.assertEqual((adjustment.kind, adjustment.quantity_change, adjustment.reservation_ref,
                          adjustment.actor_user_id, adjustment.reason),
                         ("SHOP_CANCELLATION", -1, ref, OPERATOR_ID, "Dropped the tray"))

    def test_only_the_offers_restaurant_can_cancel_and_must_say_why(self):
        ref = self.post_reserve().json()["reservation_ref"]
        other = self.post_action("shop-cancel", ref, actor_user_id=OPERATOR_ID + 1, reason="x")
        self.assertEqual((other.status_code, other.json()["code"]), (403, "not_owner"))
        no_reason = self.post_action("shop-cancel", ref, actor_user_id=OPERATOR_ID, reason="  ")
        self.assertEqual((no_reason.status_code, no_reason.json()["code"]), (400, "reason_required"))
        self.assertEqual(Allocation.objects.get().status, "HELD")

    def test_more_than_is_left_is_rejected_with_the_stored_answer(self):
        response = self.post_reserve(quantity=4)
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertEqual(body["code"], "not_enough_stock")
        self.assertEqual((body["allocation"]["status"], body["allocation"]["reject_reason"]), ("REJECTED", "not_enough_stock"))
        self.assertEqual(self.customers_see(), 3)

    def test_each_reason_an_offer_cannot_be_reserved(self):
        cases = {
            "not_published": dict(status=Listing.Status.WITHDRAWN),
            "pickup_ended": dict(pickup_end=timezone.now() - timedelta(seconds=1)),
            "terms_changed": dict(terms_revision=self.listing.terms_revision + 1),
        }
        for code, change in cases.items():
            listing = make_listing(self.shop, total_quantity=3)
            body = reserve_body(listing)
            Listing.objects.filter(pk=listing.pk).update(**change)
            response = self.api.post(reverse("internal-allocations"), body, format="json")
            self.assertEqual((response.status_code, response.json()["code"]), (409, code))
        Shop.objects.filter(pk=self.shop.pk).update(approval_status=Shop.ApprovalStatus.REJECTED)
        self.assertEqual(self.post_reserve().json()["code"], "shop_not_approved")
        self.assertFalse(Allocation.objects.filter(status="HELD").exists())

    def test_drafts_and_missing_offers_are_not_found_and_nothing_is_stored(self):
        draft = make_listing(self.shop, status=Listing.Status.DRAFT)
        self.assertEqual(self.post_reserve(listing=draft).status_code, 404)
        body = reserve_body(self.listing)
        body["listing_id"] = 999999
        self.assertEqual(self.api.post(reverse("internal-allocations"), body, format="json").json()["code"],
                         "listing_not_found")
        self.assertFalse(Allocation.objects.exists())

    def test_first_hold_records_when_the_offer_was_first_reserved(self):
        self.assertIsNone(self.listing.first_reserved_at)
        self.post_reserve()
        self.assertIsNotNone(Listing.objects.get(pk=self.listing.pk).first_reserved_at)

    def test_final_statuses_never_change(self):
        held_ref = lambda: self.post_reserve().json()["reservation_ref"]  # noqa: E731
        sold, released, cancelled = held_ref(), held_ref(), held_ref()
        self.post_action("consume", sold)
        self.post_action("release", released)
        self.post_action("shop-cancel", cancelled, actor_user_id=OPERATOR_ID, reason="Gone")
        rejected = self.post_reserve(quantity=50).json()["allocation"]["reservation_ref"]
        before = {a.reservation_ref: a.status for a in Allocation.objects.all()}
        for ref in (sold, released, cancelled, rejected):
            for action, extra in (("release", {}), ("consume", {}),
                                  ("shop-cancel", {"actor_user_id": OPERATOR_ID, "reason": "again"})):
                self.post_action(action, ref, **extra)
        self.assertEqual({a.reservation_ref: a.status for a in Allocation.objects.all()}, before)
        self.assertEqual(StockAdjustment.objects.count(), 1)

    def test_the_customers_agreed_terms_are_kept_after_the_offer_changes(self):
        ref = self.post_reserve().json()["reservation_ref"]
        Listing.objects.filter(pk=self.listing.pk).update(title="Chocolate donuts", discounted_price="3.00")
        body = self.api.get(reverse("internal-allocation", kwargs={"ref": ref})).json()
        self.assertEqual((body["terms"]["title"], body["terms"]["unit_price"]), ("Glazed donuts", "1.50"))


class SameCallTwiceTests(StockApiTestCase):
    """Done when: each call carries a unique request ID, so the same call
    sent twice changes the stock only once."""

    def test_same_ref_twice_gives_the_same_answer_and_holds_once(self):
        ref = uuid.uuid4()
        first = self.post_reserve(quantity=2, ref=ref)
        second = self.post_reserve(quantity=2, ref=ref)
        self.assertEqual((first.status_code, second.status_code), (201, 200))
        self.assertEqual(first.json(), second.json())
        self.assertEqual(Allocation.objects.count(), 1)
        self.assertEqual(self.customers_see(), 1)

    def test_a_rejection_is_repeated_even_if_stock_frees_up_later(self):
        ref = uuid.uuid4()
        first = self.post_reserve(quantity=4, ref=ref)
        Listing.objects.filter(pk=self.listing.pk).update(total_quantity=10)
        second = self.post_reserve(quantity=4, ref=ref)
        self.assertEqual((first.status_code, second.status_code), (409, 409))
        self.assertEqual(first.json(), second.json())
        self.assertEqual(self.free(), 10)

    def test_the_same_ref_for_a_different_request_is_refused(self):
        ref = uuid.uuid4()
        self.post_reserve(quantity=1, ref=ref)
        other_quantity = self.post_reserve(quantity=2, ref=ref)
        other_listing = self.post_reserve(quantity=1, ref=ref, listing=make_listing(self.shop))
        for response in (other_quantity, other_listing):
            self.assertEqual((response.status_code, response.json()["code"]), (409, "ref_conflict"))
            self.assertNotIn("allocation", response.json())  # not a definitive rejection
        self.assertEqual(self.customers_see(), 2)

    def test_release_twice_changes_stock_once(self):
        ref = self.post_reserve(quantity=2).json()["reservation_ref"]
        first, second = self.post_action("release", ref), self.post_action("release", ref)
        self.assertEqual((first.json()["changed"], second.json()["changed"]), (True, False))
        self.assertEqual(second.status_code, 200)
        self.assertEqual(self.customers_see(), 3)

    def test_consume_twice_changes_nothing_more(self):
        ref = self.post_reserve(quantity=2).json()["reservation_ref"]
        first, second = self.post_action("consume", ref), self.post_action("consume", ref)
        self.assertEqual((first.json()["changed"], second.json()["changed"]), (True, False))
        self.assertEqual(self.customers_see(), 1)

    def test_shop_cancel_twice_adjusts_stock_once(self):
        ref = self.post_reserve(quantity=1).json()["reservation_ref"]
        for _ in range(2):
            self.post_action("shop-cancel", ref, actor_user_id=OPERATOR_ID, reason="Ran out")
        self.assertEqual(Listing.objects.get(pk=self.listing.pk).total_quantity, 2)
        self.assertEqual(StockAdjustment.objects.count(), 1)

    def test_a_release_that_arrives_first_stops_a_late_reserve(self):
        ref = uuid.uuid4()
        early = self.post_action("release", ref)
        self.assertEqual((early.status_code, early.json()["status"], early.json()["quantity"]), (200, "RELEASED", 0))
        late = self.post_reserve(quantity=1, ref=ref)
        self.assertEqual((late.status_code, late.json()["status"]), (200, "RELEASED"))
        self.assertEqual(self.customers_see(), 3)

    def test_get_returns_the_stored_answer_or_404(self):
        ref = self.post_reserve().json()["reservation_ref"]
        found = self.api.get(reverse("internal-allocation", kwargs={"ref": ref}))
        self.assertEqual((found.status_code, found.json()["status"]), (200, "HELD"))
        missing = self.api.get(reverse("internal-allocation", kwargs={"ref": uuid.uuid4()}))
        self.assertEqual((missing.status_code, missing.json()["code"]), (404, "allocation_not_found"))
        self.assertEqual(self.post_action("consume", uuid.uuid4()).status_code, 404)

    def test_badly_formed_calls_are_refused_and_change_nothing(self):
        url = reverse("internal-allocations")
        good = reserve_body(self.listing)
        for change in ({"quantity": 0}, {"quantity": -1}, {"quantity": True}, {"quantity": "1"},
                       {"quantity": 1001}, {"reservation_ref": "not-a-uuid"}, {"reservation_ref": None},
                       {"listing_id": None}, {"expected_terms_revision": None}):
            response = self.api.post(url, {**good, **change}, format="json")
            self.assertEqual((response.status_code, response.json()["code"]), (400, "invalid_request"), change)
        self.assertEqual(self.api.post(url, [1, 2], format="json").status_code, 400)
        self.assertFalse(Allocation.objects.exists())


class FreeStockFormulaTests(StockApiTestCase):
    """Tests to write: free = total - HELD - CONSUMED."""

    def make_allocation(self, status, quantity):
        reason = "not_enough_stock" if status == "REJECTED" else ""
        return Allocation.objects.create(reservation_ref=uuid.uuid4(), listing=self.listing, quantity=quantity,
                                         status=status, reject_reason=reason, shop_id=self.shop.pk)

    def test_only_held_and_sold_units_count(self):
        Listing.objects.filter(pk=self.listing.pk).update(total_quantity=20)
        for status, quantity in (("HELD", 3), ("CONSUMED", 2), ("RELEASED", 4), ("SHOP_CANCELLED", 1), ("REJECTED", 5)):
            self.make_allocation(status, quantity)
        listing = Listing.objects.get(pk=self.listing.pk)
        self.assertEqual(committed_quantity(listing), 5)
        self.assertEqual(free_quantity(listing), 20 - 3 - 2)
        annotated = with_committed(Listing.objects.filter(pk=listing.pk)).get()
        self.assertEqual(free_quantity(annotated), 15)
        self.assertEqual(self.customers_see(), 15)

    def test_other_offers_allocations_do_not_count(self):
        other = make_listing(self.shop, total_quantity=9)
        Allocation.objects.create(reservation_ref=uuid.uuid4(), listing=other, quantity=9, status="HELD",
                                  shop_id=self.shop.pk)
        self.assertEqual(self.free(), 3)
        self.assertIsNone(self.customers_see(other))

    def test_a_fully_held_offer_leaves_the_public_pages(self):
        self.post_reserve(quantity=3)
        self.assertIsNone(visible_listing(self.listing.pk))
        self.assertNotIn(self.listing.pk, [listing.pk for listing, _ in visible_listings()])

    def test_browsing_still_takes_two_queries_with_allocations(self):
        for _ in range(3):
            listing = make_listing(self.shop)
            self.post_reserve(listing=listing)
        with self.assertNumQueries(2):
            APIClient().get(reverse("listings"))

    def test_the_restaurant_cannot_lower_stock_below_what_is_held_or_sold(self):
        self.post_reserve(quantity=2)
        with self.assertRaises(StockAdjustmentError) as refused:
            adjust_stock(self.listing.pk, self.shop.pk, -2, "Fewer", actor_user_id=OPERATOR_ID)
        self.assertEqual(refused.exception.code, "below_committed")
        adjust_stock(self.listing.pk, self.shop.pk, -1, "One fell", actor_user_id=OPERATOR_ID)
        self.assertEqual(self.free(), 0)

    def test_mark_sold_out_leaves_held_units_alone(self):
        self.post_reserve(quantity=1)
        client = APIClient()
        client.force_authenticate(TokenUser(id=OPERATOR_ID, role="SHOP_OPERATOR", is_staff=False))
        self.assertEqual(client.post(reverse("listing_sold_out", kwargs={"pk": self.listing.pk})).status_code, 201)
        listing = Listing.objects.get(pk=self.listing.pk)
        self.assertEqual((listing.total_quantity, free_quantity(listing)), (1, 0))
        self.assertEqual(Allocation.objects.get().status, "HELD")


class TwentyPeopleRaceTests(TransactionTestCase):
    """Tests to write: 20 people try to reserve the last item at the same
    second on PostgreSQL: exactly 1 gets it and stock never goes below 0."""

    PEOPLE = 20

    def setUp(self):
        self.shop = make_shop()

    def race(self, listing, quantity_each=1, refs=None):
        refs = refs or [uuid.uuid4() for _ in range(self.PEOPLE)]
        start = threading.Barrier(len(refs))
        results, errors = [], []

        def person(ref):
            try:
                start.wait(timeout=30)  # everyone presses "Reserve" at once
                allocation, _ = reserve(ref, listing.pk, quantity_each, listing.terms_revision)
                results.append(allocation.status)
            except Exception as error:  # noqa: BLE001 - reported below
                errors.append(repr(error))
            finally:
                connections.close_all()  # each thread had its own connection

        threads = [threading.Thread(target=person, args=(ref,)) for ref in refs]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        self.assertEqual(errors, [])
        return results

    def test_twenty_people_one_last_item_exactly_one_gets_it(self):
        listing = make_listing(self.shop, total_quantity=1)
        results = self.race(listing)
        self.assertEqual(sorted(results), ["HELD"] + ["REJECTED"] * 19)
        self.assertEqual(Allocation.objects.filter(status="HELD").count(), 1)
        self.assertEqual(set(Allocation.objects.filter(status="REJECTED").values_list("reject_reason", flat=True)),
                         {"not_enough_stock"})
        self.assertEqual(free_quantity(Listing.objects.get(pk=listing.pk)), 0)

    def test_twenty_people_five_items_exactly_five_get_one(self):
        listing = make_listing(self.shop, total_quantity=5)
        results = self.race(listing)
        self.assertEqual(results.count("HELD"), 5)
        self.assertEqual(free_quantity(Listing.objects.get(pk=listing.pk)), 0)

    def test_stock_never_goes_below_zero_with_bigger_orders(self):
        listing = make_listing(self.shop, total_quantity=7)
        results = self.race(listing, quantity_each=3)
        self.assertEqual(results.count("HELD"), 2)  # 3 + 3 = 6 <= 7; a third would need 9
        free = free_quantity(Listing.objects.get(pk=listing.pk))
        self.assertEqual(free, 1)
        self.assertGreaterEqual(free, 0)

    def test_the_same_call_sent_twenty_times_at_once_holds_once(self):
        listing = make_listing(self.shop, total_quantity=5)
        ref = uuid.uuid4()
        results = self.race(listing, refs=[ref] * self.PEOPLE)
        self.assertEqual(results, ["HELD"] * self.PEOPLE)
        self.assertEqual(Allocation.objects.count(), 1)
        self.assertEqual(free_quantity(Listing.objects.get(pk=listing.pk)), 4)


class ServiceFunctionTests(TestCase):
    """The same rules when the service functions are called directly."""

    def setUp(self):
        self.shop = make_shop()
        self.listing = make_listing(self.shop, total_quantity=3)

    def test_errors_carry_a_code_and_an_http_status(self):
        with self.assertRaises(AllocationError) as missing:
            consume(uuid.uuid4(), self.listing.pk)
        self.assertEqual((missing.exception.code, missing.exception.http_status), ("allocation_not_found", 404))
        with self.assertRaises(AllocationError) as no_reason:
            shop_cancel(uuid.uuid4(), self.listing.pk, OPERATOR_ID, "")
        self.assertEqual(no_reason.exception.http_status, 400)

    def test_release_of_a_rejected_request_changes_nothing(self):
        allocation, _ = reserve(uuid.uuid4(), self.listing.pk, 10, self.listing.terms_revision)
        self.assertEqual(allocation.status, "REJECTED")
        same, changed = release(allocation.reservation_ref, self.listing.pk)
        self.assertEqual((same.status, changed), ("REJECTED", False))
