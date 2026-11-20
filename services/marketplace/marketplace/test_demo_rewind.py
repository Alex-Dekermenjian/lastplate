"""Demo clock: when the clock moves back, later allocations never happened."""

import uuid
from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from config import demo_clock

from .models import Allocation, Listing
from .stock import free_quantity
from .test_browse import make_listing, make_shop
from .test_lp20_stock_api import KEY, TEST_KEYS, reserve_body, service_client


@override_settings(INTERNAL_SERVICE_KEYS=TEST_KEYS)
class DemoRewindTests(TestCase):
    def setUp(self):
        self.enterContext(mock.patch.object(demo_clock, "enabled", return_value=True))
        self.listing = make_listing(make_shop(), title="Glazed donuts", total_quantity=5, discounted_price="1.50")
        self.api = service_client()
        self.url = reverse("internal-demo-rewind")

    def hold(self, quantity=2):
        ref = uuid.uuid4()
        response = self.api.post(reverse("internal-allocations"), reserve_body(self.listing, quantity, ref),
                                 format="json")
        self.assertEqual(response.status_code, 201, response.content)
        return ref

    def free(self):
        return free_quantity(Listing.objects.get(pk=self.listing.pk))

    def rewind(self, since, refs=()):
        return self.api.post(self.url, {"since": since.isoformat(), "refs": [str(ref) for ref in refs]},
                             format="json")

    def test_allocations_made_after_that_time_are_deleted_and_the_stock_comes_back(self):
        self.hold(2)
        self.assertEqual(self.free(), 3)

        response = self.rewind(timezone.now() - timedelta(hours=1))

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"allocations_removed": 1})
        self.assertEqual(Allocation.objects.count(), 0)
        self.assertEqual(self.free(), 5)

    def test_allocations_made_before_that_time_stay(self):
        self.hold(2)

        response = self.rewind(timezone.now() + timedelta(hours=1))

        self.assertEqual(response.json(), {"allocations_removed": 0})
        self.assertEqual(self.free(), 3)

    def test_the_allocations_of_the_orders_being_deleted_go_even_if_older(self):
        ref = self.hold(2)
        self.hold(1)

        response = self.rewind(timezone.now() + timedelta(hours=1), refs=[ref])

        self.assertEqual(response.json(), {"allocations_removed": 1})
        self.assertEqual(self.free(), 4)

    def test_it_does_not_exist_when_the_clock_is_off(self):
        self.hold(2)
        with mock.patch.object(demo_clock, "enabled", return_value=False):
            response = self.rewind(timezone.now() - timedelta(hours=1))

        self.assertEqual(response.status_code, 404)
        self.assertEqual(Allocation.objects.count(), 1)

    def test_only_reservations_with_its_key_may_call_it(self):
        self.hold(2)

        for client in (service_client(key="wrong-key"), service_client(name=None, key=None)):
            with self.subTest(client=client):
                response = client.post(self.url, {"since": timezone.now().isoformat()}, format="json")
                self.assertIn(response.status_code, (401, 403))
        self.assertEqual(Allocation.objects.count(), 1)

    def test_a_bad_time_or_list_is_refused_and_nothing_is_deleted(self):
        self.hold(2)

        for body in ({}, {"since": "tomorrow"}, {"since": "2026-11-20T10:00:00"},
                     {"since": timezone.now().isoformat(), "refs": "x"},
                     {"since": timezone.now().isoformat(), "refs": ["not-a-uuid"]}):
            with self.subTest(body=body):
                response = self.api.post(self.url, body, format="json")
                self.assertEqual(response.status_code, 400)
        self.assertEqual(Allocation.objects.count(), 1)
