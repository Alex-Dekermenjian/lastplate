"""LP-17: the Order table in the Reservations service.

One test class per "Done when" line of the Jira card, plus the "Tests to
write" list (pick-up-by examples, pickup codes, no deleting, Beirut time).
"""

import os
import re
import subprocess
import sys
import uuid
from datetime import datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.management import CommandError, call_command
from django.db import DataError, IntegrityError, connection, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from config.settings import _pickup_hold_minutes
from reservations import orders
from reservations.models import (
    PICKUP_CODE_ALPHABET,
    Actor,
    CancelledBy,
    NotDeletable,
    Order,
    OrderEvent,
    OrderLine,
    Status,
    StatusChangeRefused,
    SyncState,
)
from reservations.orders import InvalidStatusChange, change_status, create_order, pick_up_by

BEIRUT = ZoneInfo("Asia/Beirut")
UTC = ZoneInfo("UTC")
SERVICE_DIR = Path(__file__).resolve().parent.parent


def beirut(hour, minute=0, day=19, month=11):
    """A Beirut wall-clock time; 19 Nov 2026 is the Thursday of Demo 2."""
    return datetime(2026, month, day, hour, minute, tzinfo=BEIRUT)


def donuts(**changes):
    """The card's example: donuts on sale 6-9 pm."""
    line = {
        "listing_id": 41, "quantity": 2, "title": "Glazed donuts",
        "unit_price": Decimal("1.50"), "pickup_start": beirut(18), "pickup_end": beirut(21),
    }
    line.update(changes)
    return line


def place(ordered_at=None, lines=None, branch_id=7, customer_user_id=101, shop_id=3):
    return create_order(
        customer_user_id=customer_user_id, shop_id=shop_id, branch_id=branch_id,
        request_key=f"key-{uuid.uuid4()}", lines=[donuts()] if lines is None else lines,
        now=ordered_at or beirut(18, 15),
    )


def reserved(**kwargs):
    order = place(**kwargs)
    return change_status(order, Status.CONFIRMED, actor=Actor.SYSTEM, sync_state=SyncState.SYNCED)


class OrderTableTests(TestCase):
    """Done when: an Order table with customer, restaurant, branch, items,
    total to pay in cash, a 6-character pickup code, status, the time it was
    created and a "pick up by" time."""

    def test_order_stores_everything_the_card_lists(self):
        order = place(lines=[donuts(), donuts(listing_id=42, title="Croissant", quantity=1, unit_price=Decimal("2.25"))])
        order = Order.objects.get(pk=order.pk)

        self.assertEqual(order.customer_user_id, 101)
        self.assertEqual(order.shop_id, 3)
        self.assertEqual(order.branch_id, 7)
        self.assertEqual([(line.title, line.quantity) for line in order.lines.all()],
                         [("Glazed donuts", 2), ("Croissant", 1)])
        self.assertEqual(order.total_cash, Decimal("5.25"))
        self.assertRegex(order.pickup_code, r"^[A-Z2-9]{6}$")
        self.assertEqual(order.status, Status.PROCESSING)
        self.assertEqual(order.sync_state, SyncState.ALLOCATE_PENDING)
        self.assertEqual(order.created_at, beirut(18, 15))
        self.assertEqual(order.pick_up_by, beirut(19, 15))
        self.assertIsInstance(order.reservation_ref, uuid.UUID)
        self.assertTrue(order.request_key)

    def test_total_cash_is_a_decimal_with_two_places(self):
        field = Order._meta.get_field("total_cash")
        self.assertEqual(field.get_internal_type(), "DecimalField")
        self.assertEqual(field.decimal_places, 2)
        order = place(lines=[donuts(quantity=3, unit_price=Decimal("0.35"))])
        self.assertEqual(Order.objects.get(pk=order.pk).total_cash, Decimal("1.05"))

    def test_other_services_data_is_stored_as_plain_ids_never_foreign_keys(self):
        for model, name in ((Order, "customer_user_id"), (Order, "shop_id"), (Order, "branch_id"),
                            (OrderLine, "listing_id")):
            field = model._meta.get_field(name)
            self.assertFalse(field.is_relation, f"{model.__name__}.{name} must be a plain ID")
            self.assertEqual(field.get_internal_type(), "PositiveBigIntegerField")
        # The only relations are inside this service: items and history -> Order.
        for model in (Order, OrderLine, OrderEvent):
            for field in model._meta.get_fields():
                if field.is_relation and field.concrete:
                    self.assertIs(field.related_model, Order)

    def test_request_key_is_unique_per_customer(self):
        place()
        Order.objects.update(request_key="same")
        with self.assertRaises(IntegrityError), transaction.atomic():
            create_order(customer_user_id=101, shop_id=3, branch_id=7, request_key="same",
                         lines=[donuts()], now=beirut(18, 20))
        # Another customer may use the same key.
        create_order(customer_user_id=102, shop_id=3, branch_id=7, request_key="same",
                     lines=[donuts()], now=beirut(18, 20))

    def test_an_order_needs_at_least_one_item(self):
        with self.assertRaises(ValueError):
            place(lines=[])
        self.assertFalse(Order.objects.exists())


class FourVisibleStatusesTests(TestCase):
    """Done when: customers and shops only ever see Reserved, Paid, Cancelled
    (with who cancelled) and Expired."""

    def test_internal_statuses_follow_the_plan(self):
        self.assertEqual(set(Status.values),
                         {"PROCESSING", "CONFIRMED", "NOT_RESERVED", "CANCELLED", "COLLECTED", "EXPIRED"})
        self.assertEqual(set(SyncState.values), {"ALLOCATE_PENDING", "RELEASE_PENDING", "CONSUME_PENDING",
                                                 "SHOP_CANCEL_PENDING", "SYNCED", "NEEDS_ATTENTION"})

    def test_words_users_see(self):
        order = reserved()
        self.assertEqual(order.status_label, "Reserved")
        self.assertEqual(order.get_status_display(), "Reserved")
        paid = change_status(order, Status.COLLECTED, actor=Actor.SHOP)
        self.assertEqual(paid.status_label, "Paid")
        self.assertEqual(change_status(reserved(), Status.CANCELLED, actor=Actor.CUSTOMER).status_label, "Cancelled")
        self.assertEqual(change_status(reserved(), Status.EXPIRED, actor=Actor.SYSTEM).status_label, "Expired")

    def test_processing_and_not_reserved_are_never_shown_as_orders(self):
        processing = place()
        rejected = change_status(place(), Status.NOT_RESERVED, actor=Actor.SYSTEM)
        shown = reserved()
        self.assertIsNone(processing.status_label)
        self.assertIsNone(rejected.status_label)
        self.assertEqual(list(Order.objects.visible_to_users()), [shown])

    def test_cancelled_records_who_cancelled(self):
        by_customer = change_status(reserved(), Status.CANCELLED, actor=Actor.CUSTOMER)
        by_shop = change_status(reserved(), Status.CANCELLED, actor=Actor.SHOP, reason="Ran out of donuts")
        by_admin = change_status(reserved(), Status.CANCELLED, actor=Actor.ADMIN, reason="Restaurant closed")

        self.assertEqual(by_customer.cancelled_by, CancelledBy.CUSTOMER)
        self.assertEqual(by_shop.get_cancelled_by_display(), "Restaurant")
        self.assertEqual(by_shop.cancel_reason, "Ran out of donuts")
        self.assertEqual(by_admin.get_cancelled_by_display(), "Admin")
        self.assertEqual(Order.objects.get(pk=by_shop.pk).cancelled_by, "SHOP")

    def test_restaurant_and_admin_must_say_why_they_cancel(self):
        for actor in (Actor.SHOP, Actor.ADMIN):
            order = reserved()
            with self.assertRaises(InvalidStatusChange):
                change_status(order, Status.CANCELLED, actor=actor, reason="  ")
            self.assertEqual(Order.objects.get(pk=order.pk).status, Status.CONFIRMED)

    def test_the_system_cannot_cancel_and_customers_cannot_mark_paid(self):
        with self.assertRaises(InvalidStatusChange):
            change_status(reserved(), Status.CANCELLED, actor=Actor.SYSTEM, reason="x")
        with self.assertRaises(InvalidStatusChange):
            change_status(reserved(), Status.COLLECTED, actor=Actor.CUSTOMER)

    def test_paid_records_when_it_was_picked_up(self):
        order = change_status(reserved(), Status.COLLECTED, actor=Actor.SHOP, now=beirut(18, 40))
        self.assertEqual(Order.objects.get(pk=order.pk).collected_at, beirut(18, 40))

    def test_final_statuses_never_change_and_processing_cannot_be_paid(self):
        for final in (Status.CANCELLED, Status.COLLECTED, Status.EXPIRED):
            order = reserved()
            actor = Actor.SHOP if final == Status.COLLECTED else Actor.CUSTOMER if final == Status.CANCELLED else Actor.SYSTEM
            change_status(order, final, actor=actor)
            for target in Status:
                with self.assertRaises(InvalidStatusChange):
                    change_status(order, target, actor=Actor.ADMIN, reason="try")
        with self.assertRaises(InvalidStatusChange):
            change_status(place(), Status.COLLECTED, actor=Actor.SHOP)

    def test_database_keeps_who_cancelled_and_paid_time_consistent(self):
        order = reserved()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Order.objects.filter(pk=order.pk).update(cancelled_by=CancelledBy.CUSTOMER)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Order.objects.filter(pk=order.pk).update(collected_at=beirut(19))


class PickUpByTests(TestCase):
    """Done when: pick up by = 1 hour after ordering (or after the window
    opens, if ordered earlier), never later than the end of the window. The
    hour is one setting."""

    def test_ordered_at_6_15_for_a_6_to_9_pm_offer_pick_up_by_7_15(self):
        self.assertEqual(place(beirut(18, 15)).pick_up_by, beirut(19, 15))

    def test_ordered_at_8_30_pick_up_by_9_00_the_end_of_the_window(self):
        self.assertEqual(place(beirut(20, 30)).pick_up_by, beirut(21, 0))

    def test_ordered_at_5_pm_before_the_window_opens_pick_up_by_7_00(self):
        self.assertEqual(place(beirut(17, 0)).pick_up_by, beirut(19, 0))

    def test_stored_value_is_the_same_after_reading_it_back(self):
        order = place(beirut(20, 30))
        self.assertEqual(Order.objects.get(pk=order.pk).pick_up_by, beirut(21, 0))

    def test_several_items_use_the_earliest_start_and_the_earliest_end(self):
        lines = [orders.LineSnapshot(**donuts()),
                 orders.LineSnapshot(**donuts(listing_id=2, pickup_start=beirut(19), pickup_end=beirut(20)))]
        self.assertEqual(pick_up_by(beirut(17), lines), beirut(19))       # 18:00 + 1 h
        self.assertEqual(pick_up_by(beirut(19, 30), lines), beirut(20))   # capped at the earliest end

    def test_the_hour_is_the_pickup_hold_minutes_setting(self):
        self.assertEqual(settings.PICKUP_HOLD_MINUTES, int(os.environ.get("PICKUP_HOLD_MINUTES", "60")))
        with override_settings(PICKUP_HOLD_MINUTES=2):
            self.assertEqual(place(beirut(18, 15)).pick_up_by, beirut(18, 17))

    def test_default_is_60_minutes_and_an_environment_variable_changes_it(self):
        def hold(env_value):
            env = {key: value for key, value in os.environ.items() if key != "PICKUP_HOLD_MINUTES"}
            if env_value is not None:
                env["PICKUP_HOLD_MINUTES"] = env_value
            env["DJANGO_SETTINGS_MODULE"] = "config.settings"
            return subprocess.run(
                [sys.executable, "-c", "from django.conf import settings; print(settings.PICKUP_HOLD_MINUTES)"],
                cwd=SERVICE_DIR, env=env, capture_output=True, text=True, timeout=60,
            )

        self.assertEqual(hold(None).stdout.strip(), "60")
        self.assertEqual(hold("2").stdout.strip(), "2")
        self.assertNotEqual(hold("0").returncode, 0)

    def test_hold_minutes_must_be_a_positive_whole_number(self):
        self.assertEqual(_pickup_hold_minutes("60"), 60)
        self.assertEqual(_pickup_hold_minutes("2"), 2)
        for bad in ("0", "-5", "abc", "", None, "1.5"):
            with self.assertRaises(ImproperlyConfigured):
                _pickup_hold_minutes(bad)


class SnapshotTests(TestCase):
    """Done when: each order keeps a copy of the item name, price and pickup
    window from when it was placed; later edits by the restaurant never change it."""

    def test_order_line_keeps_name_price_and_window(self):
        line = place().lines.get()
        self.assertEqual((line.listing_id, line.title, line.unit_price, line.quantity),
                         (41, "Glazed donuts", Decimal("1.50"), 2))
        self.assertEqual((line.pickup_start, line.pickup_end), (beirut(18), beirut(21)))

    def test_changing_the_offer_afterwards_does_not_change_the_order(self):
        offer = donuts()
        order = place(lines=[offer])
        # What the restaurant edits later lives in Marketplace, not here.
        offer.update(title="Chocolate donuts", unit_price=Decimal("3.00"), pickup_end=beirut(22))
        line = OrderLine.objects.get(order=order)
        self.assertEqual((line.title, line.unit_price, line.pickup_end), ("Glazed donuts", Decimal("1.50"), beirut(21)))
        self.assertEqual(Order.objects.get(pk=order.pk).total_cash, Decimal("3.00"))

    def test_the_copy_is_never_edited_in_code_or_in_the_database(self):
        line = place().lines.get()
        line.unit_price = Decimal("9.99")
        with self.assertRaises(StatusChangeRefused):
            line.save()
        with self.assertRaises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("UPDATE reservations_orderline SET unit_price = 9.99 WHERE id = %s", [line.pk])
        self.assertEqual(OrderLine.objects.get(pk=line.pk).unit_price, Decimal("1.50"))

    def test_listing_is_a_plain_id_not_a_link_to_marketplace(self):
        self.assertFalse(OrderLine._meta.get_field("listing_id").is_relation)


class HistoryTests(TestCase):
    """Done when: every status change is saved in a history (what, who, why,
    when). Orders are never deleted."""

    def test_every_change_writes_one_history_row(self):
        order = place(beirut(18, 15))
        change_status(order, Status.CONFIRMED, actor=Actor.SYSTEM, sync_state=SyncState.SYNCED, now=beirut(18, 15))
        change_status(order, Status.CANCELLED, actor=Actor.SHOP, reason="Ran out", now=beirut(18, 50))

        rows = list(OrderEvent.objects.filter(order=order).values_list("from_status", "to_status", "actor", "reason", "created_at"))
        self.assertEqual(rows, [
            ("", "PROCESSING", "CUSTOMER", "Order placed", beirut(18, 15)),
            ("PROCESSING", "CONFIRMED", "SYSTEM", "", beirut(18, 15)),
            ("CONFIRMED", "CANCELLED", "SHOP", "Ran out", beirut(18, 50)),
        ])

    def test_refused_changes_leave_no_history_row(self):
        order = reserved()
        before = order.events.count()
        with self.assertRaises(InvalidStatusChange):
            change_status(order, Status.NOT_RESERVED, actor=Actor.SYSTEM)
        with self.assertRaises(InvalidStatusChange):
            change_status(order, Status.EXPIRED, actor=Actor.SYSTEM, expected_status=Status.PROCESSING)
        self.assertEqual(order.events.count(), before)

    def test_status_and_history_are_saved_in_the_same_transaction(self):
        order = reserved()
        with mock.patch.object(OrderEvent.objects, "create", side_effect=RuntimeError("history write failed")):
            with self.assertRaises(RuntimeError):
                change_status(order, Status.COLLECTED, actor=Actor.SHOP)
        order.refresh_from_db()
        self.assertEqual(order.status, Status.CONFIRMED)
        self.assertIsNone(order.collected_at)

    def test_status_changes_only_through_change_status(self):
        order = Order.objects.get(pk=reserved().pk)
        order.status = Status.COLLECTED
        with self.assertRaises(StatusChangeRefused):
            order.save()
        with self.assertRaises(StatusChangeRefused):
            Order.objects.filter(pk=order.pk).update(status=Status.EXPIRED)
        self.assertEqual(Order.objects.get(pk=order.pk).status, Status.CONFIRMED)

    def test_change_status_locks_the_row(self):
        order = reserved()
        with mock.patch.object(Order.objects, "select_for_update", wraps=Order.objects.select_for_update) as lock:
            change_status(order, Status.EXPIRED, actor=Actor.SYSTEM)
        lock.assert_called_once_with()

    def test_history_rows_are_never_edited_or_deleted(self):
        event = place().events.get()
        event.reason = "rewritten"
        with self.assertRaises(StatusChangeRefused):
            event.save()
        with self.assertRaises(NotDeletable):
            event.delete()
        with self.assertRaises(NotDeletable):
            OrderEvent.objects.all().delete()
        with self.assertRaises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("DELETE FROM reservations_orderevent")
        self.assertEqual(OrderEvent.objects.count(), 1)

    def test_orders_and_items_are_never_deleted_in_code_or_in_the_database(self):
        order = place()
        with self.assertRaises(NotDeletable):
            order.delete()
        with self.assertRaises(NotDeletable):
            Order.objects.all().delete()
        with self.assertRaises(NotDeletable):
            order.lines.get().delete()
        with self.assertRaises(NotDeletable):
            OrderLine.objects.all().delete()
        for table in ("reservations_order", "reservations_orderline"):
            with self.assertRaises(IntegrityError), transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(f"DELETE FROM {table}")
        self.assertEqual((Order.objects.count(), OrderLine.objects.count()), (1, 1))


class AdminAndApiCannotDeleteTests(TestCase):
    """Tests to write: an order cannot be deleted through the admin or API."""

    @classmethod
    def setUpTestData(cls):
        cls.admin_user = get_user_model().objects.create_superuser("teamadmin", "team@example.test", "unused-Aa1")

    def setUp(self):
        self.client.force_login(self.admin_user)
        self.order = reserved()

    def test_admin_shows_the_order_but_has_no_delete(self):
        change_url = reverse("admin:reservations_order_change", args=[self.order.pk])
        page = self.client.get(change_url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, self.order.pickup_code)
        self.assertNotContains(page, "deletelink")
        self.assertEqual(self.client.get(reverse("admin:reservations_order_delete", args=[self.order.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("admin:reservations_order_delete", args=[self.order.pk]), {"post": "yes"}).status_code, 403)
        self.assertTrue(Order.objects.filter(pk=self.order.pk).exists())

    def test_admin_list_has_no_delete_selected_action(self):
        changelist = reverse("admin:reservations_order_changelist")
        page = self.client.get(changelist)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "delete_selected")
        self.client.post(changelist, {"action": "delete_selected", "_selected_action": [self.order.pk], "post": "yes"})
        self.assertTrue(Order.objects.filter(pk=self.order.pk).exists())

    def test_admin_cannot_add_or_edit_orders_items_or_history(self):
        self.assertEqual(self.client.get(reverse("admin:reservations_order_add")).status_code, 403)
        change_url = reverse("admin:reservations_order_change", args=[self.order.pk])
        self.client.post(change_url, {"status": Status.CANCELLED, "cancelled_by": "ADMIN"})
        self.assertEqual(Order.objects.get(pk=self.order.pk).status, Status.CONFIRMED)
        # Items and history are only shown inside the order, never on their own.
        self.assertFalse(any(model in self.client.get(reverse("admin:index")).content.decode()
                             for model in ("/admin/reservations/orderline/", "/admin/reservations/orderevent/")))

    def test_http_delete_never_removes_an_order(self):
        # No API offers DELETE on an order (the order APIs come with LP-19/22/23,
        # and the model and database refuse deletes whatever they do).
        for url in (f"/api/reservations/{self.order.reservation_ref}/",
                    f"/api/shop/reservations/{self.order.reservation_ref}/"):
            self.assertIn(self.client.delete(url).status_code, (403, 404, 405), url)
        # The admin change page treats DELETE like a page view; nothing is removed.
        self.client.delete(f"/admin/reservations/order/{self.order.pk}/")
        self.assertTrue(Order.objects.filter(pk=self.order.pk).exists())


class PickupCodeTests(TestCase):
    """Tests to write: pickup code format and uniqueness."""

    def test_six_letters_and_digits_without_0_o_1_i(self):
        self.assertEqual(len(PICKUP_CODE_ALPHABET), 32)
        self.assertFalse(set("0O1I") & set(PICKUP_CODE_ALPHABET))
        self.assertTrue(set(PICKUP_CODE_ALPHABET) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"))
        codes = {orders.new_pickup_code() for _ in range(2000)}
        for code in codes:
            self.assertRegex(code, r"^[A-HJ-NP-Z2-9]{6}$")
        self.assertGreater(len(codes), 1990)  # random, not a counter

    def test_every_order_gets_a_valid_code(self):
        for minute in range(10):
            self.assertRegex(place(beirut(18, minute)).pickup_code, r"^[A-HJ-NP-Z2-9]{6}$")

    def test_a_code_already_open_in_the_branch_is_not_given_again(self):
        with mock.patch.object(orders, "new_pickup_code", side_effect=["AAAAAA", "AAAAAA", "BBBBBB"]):
            first = place()
            second = place()
        self.assertEqual((first.pickup_code, second.pickup_code), ("AAAAAA", "BBBBBB"))

    def test_the_same_code_is_fine_in_another_branch_or_once_the_first_order_is_closed(self):
        with mock.patch.object(orders, "new_pickup_code", return_value="CCCCCC"):
            first = reserved(branch_id=1)
            other_branch = place(branch_id=2)
            change_status(first, Status.COLLECTED, actor=Actor.SHOP)
            after_close = place(branch_id=1)
        self.assertEqual({first.pickup_code, other_branch.pickup_code, after_close.pickup_code}, {"CCCCCC"})

    def test_the_database_refuses_two_open_orders_with_one_code_in_a_branch(self):
        first = place()
        duplicate = dict(customer_user_id=5, shop_id=3, branch_id=first.branch_id, request_key="dup",
                         pickup_code=first.pickup_code, total_cash=Decimal("1.00"), pick_up_by=beirut(19))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Order.objects.create(**duplicate)

    def test_the_database_refuses_a_badly_formed_code(self):
        # Too long is stopped by the column length, the rest by the format check.
        for bad in ("ABC0O1", "abcdef", "ABCDE", "ABCDEFG", "ABCDI2"):
            with self.assertRaises((IntegrityError, DataError)), transaction.atomic():
                Order.objects.create(customer_user_id=5, shop_id=3, branch_id=7, request_key=bad, pickup_code=bad,
                                     total_cash=Decimal("1.00"), pick_up_by=beirut(19))

    def test_gives_up_instead_of_looping_forever(self):
        place()
        taken = Order.objects.get().pickup_code
        with mock.patch.object(orders, "new_pickup_code", return_value=taken):
            with self.assertRaises(RuntimeError):
                place()
        self.assertEqual(Order.objects.count(), 1)


class BeirutTimeTests(TestCase):
    """Done when: all times are saved in UTC and always shown in Beirut time,
    so a 6-9 pm offer is 6-9 pm on every page."""

    def test_settings(self):
        self.assertTrue(settings.USE_TZ)
        self.assertEqual(settings.TIME_ZONE, "Asia/Beirut")

    def test_a_6_to_9_pm_offer_is_saved_in_utc(self):
        order = place()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pickup_start AT TIME ZONE 'UTC', pickup_end AT TIME ZONE 'UTC' "
                "FROM reservations_orderline WHERE order_id = %s", [order.pk])
            start_utc, end_utc = cursor.fetchone()
            cursor.execute("SHOW TIME ZONE")
            self.assertEqual(cursor.fetchone()[0], "UTC")
        # Lebanon is UTC+2 in November.
        self.assertEqual((start_utc, end_utc), (datetime(2026, 11, 19, 16), datetime(2026, 11, 19, 19)))
        line = OrderLine.objects.get(order=order)
        self.assertEqual(line.pickup_start.utcoffset().total_seconds(), 0)

    def test_a_6_to_9_pm_offer_shows_as_6_to_9_pm_in_beirut_time(self):
        line = place().lines.get()
        self.assertEqual(orders.pickup_window_text(line.pickup_start, line.pickup_end), "Thu 19 Nov, 18:00–21:00")
        self.assertEqual(orders.time_text(line.pickup_start), "Thu 19 Nov, 18:00")

    def test_the_same_holds_in_summer_time(self):
        summer = donuts(pickup_start=beirut(18, month=7, day=16), pickup_end=beirut(21, month=7, day=16))
        line = place(beirut(18, 15, month=7, day=16), lines=[summer]).lines.get()
        self.assertEqual(line.pickup_start.astimezone(UTC).hour, 15)  # UTC+3 in July
        self.assertEqual(orders.pickup_window_text(line.pickup_start, line.pickup_end), "Thu 16 Jul, 18:00–21:00")

    def test_the_admin_page_shows_beirut_time(self):
        admin_user = get_user_model().objects.create_superuser("timeadmin", "time@example.test", "unused-Aa1")
        self.client.force_login(admin_user)
        order = place(beirut(18, 15))
        page = self.client.get(reverse("admin:reservations_order_change", args=[order.pk])).content.decode()
        self.assertIn("Thu 19 Nov, 18:00–21:00", page)
        self.assertIn("Thu 19 Nov, 18:15", page)   # placed
        self.assertIn("Thu 19 Nov, 19:15", page)   # pick up by
        self.assertNotIn("16:00–19:00", page)


class SampleOrderCommandTests(TestCase):
    """The development command used to look at an order in the admin."""

    @override_settings(DEBUG=True)
    def test_creates_one_reserved_fictional_order(self):
        out = StringIO()
        call_command("make_sample_order", "--ordered-at", "18:15", stdout=out)
        order = Order.objects.get()
        self.assertEqual(order.status_label, "Reserved")
        self.assertIn("SAMPLE", order.lines.get().title)
        self.assertEqual(order.pick_up_by - order.created_at, orders.timedelta(hours=1))
        self.assertIn("18:00–21:00", out.getvalue())
        self.assertEqual(order.events.count(), 2)

    @override_settings(DEBUG=True)
    def test_can_then_cancel_it(self):
        call_command("make_sample_order", "--ordered-at", "18:15", "--then", "shop-cancel", stdout=StringIO())
        order = Order.objects.get()
        self.assertEqual((order.status_label, order.get_cancelled_by_display()), ("Cancelled", "Restaurant"))

    @override_settings(DEBUG=False)
    def test_refused_outside_development(self):
        with self.assertRaises(CommandError):
            call_command("make_sample_order", stdout=StringIO())
        self.assertFalse(Order.objects.exists())
