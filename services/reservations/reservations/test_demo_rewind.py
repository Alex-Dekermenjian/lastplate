"""Demo clock: moving the clock back deletes the orders from "the future"."""

from unittest import mock

from django.test import TestCase
from rest_framework.test import APIClient

from config import demo_clock

from . import marketplace_client
from .models import Actor, NotDeletable, Order, OrderEvent, OrderLine, Status, StrikeRemoval, SyncState
from .orders import change_status
from .test_lp17_orders import beirut, place

URL = "/api/demo/rewind"


class RewindTestCase(TestCase):
    def setUp(self):
        self.enterContext(mock.patch.object(demo_clock, "enabled", return_value=True))
        self.stock_calls = []

        def fake_rewind(since, refs):
            self.stock_calls.append((since, [str(ref) for ref in refs]))
            return len(refs)

        self.enterContext(mock.patch.object(marketplace_client, "demo_rewind", side_effect=fake_rewind))
        self.api = APIClient()

    def rewind(self, since):
        return self.api.post(URL, {"since": since.isoformat()}, format="json")


class RewindTests(RewindTestCase):
    def setUp(self):
        super().setUp()
        self.earlier = place(ordered_at=beirut(17, 0))
        self.later = place(ordered_at=beirut(22, 5), customer_user_id=102)

    def test_an_order_placed_after_the_new_time_is_deleted_for_good(self):
        response = self.rewind(beirut(17, 48))

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["orders_removed"], 1)
        self.assertEqual(list(Order.objects.values_list("pk", flat=True)), [self.earlier.pk])

    def test_its_lines_and_history_go_with_it(self):
        change_status(self.later, Status.CONFIRMED, actor=Actor.SYSTEM, sync_state=SyncState.SYNCED)

        self.rewind(beirut(17, 48))

        self.assertFalse(OrderLine.objects.filter(order_id=self.later.pk).exists())
        self.assertFalse(OrderEvent.objects.filter(order_id=self.later.pk).exists())
        self.assertTrue(OrderLine.objects.filter(order_id=self.earlier.pk).exists())
        self.assertTrue(OrderEvent.objects.filter(order_id=self.earlier.pk).exists())

    def test_an_expired_order_and_its_strike_removal_go_too(self):
        change_status(self.later, Status.EXPIRED, actor=Actor.SYSTEM, sync_state=SyncState.SYNCED)
        StrikeRemoval.objects.create(order=Order.objects.get(pk=self.later.pk), reason="demo", removed_by_user_id=1)

        response = self.rewind(beirut(17, 48))

        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(StrikeRemoval.objects.exists())
        self.assertFalse(Order.objects.filter(pk=self.later.pk).exists())

    def test_marketplace_is_asked_to_put_the_food_back_with_the_deleted_orders_references(self):
        self.rewind(beirut(17, 48))

        self.assertEqual(self.stock_calls, [(beirut(17, 48), [str(self.later.reservation_ref)])])

    def test_moving_to_a_time_after_every_order_deletes_nothing(self):
        response = self.rewind(beirut(23, 0))

        self.assertEqual(response.json()["orders_removed"], 0)
        self.assertEqual(Order.objects.count(), 2)

    def test_it_does_not_come_back_when_the_clock_passes_that_time_again(self):
        self.rewind(beirut(17, 48))
        self.rewind(beirut(23, 30))

        self.assertEqual(Order.objects.count(), 1)

    def test_nothing_is_deleted_when_marketplace_cannot_put_the_food_back(self):
        with mock.patch.object(marketplace_client, "demo_rewind",
                               side_effect=marketplace_client.MarketplaceUnavailable("down")):
            response = self.rewind(beirut(17, 48))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(Order.objects.count(), 2)

    def test_the_rest_of_the_app_still_cannot_delete_orders(self):
        with self.assertRaises(NotDeletable):
            Order.objects.all().delete()
        with self.assertRaises(NotDeletable):
            self.earlier.delete()


class RewindRefusalTests(RewindTestCase):
    def test_it_does_not_exist_when_the_clock_is_off(self):
        order = place(ordered_at=beirut(22, 5))
        with mock.patch.object(demo_clock, "enabled", return_value=False):
            response = self.rewind(beirut(17, 48))

        self.assertEqual(response.status_code, 404)
        self.assertTrue(Order.objects.filter(pk=order.pk).exists())

    def test_a_missing_or_naive_time_is_refused(self):
        place(ordered_at=beirut(22, 5))

        for body in ({}, {"since": "tomorrow"}, {"since": "2026-11-20T10:00:00"}, {"since": 5}):
            with self.subTest(body=body):
                self.assertEqual(self.api.post(URL, body, format="json").status_code, 400)
        self.assertEqual(Order.objects.count(), 1)
