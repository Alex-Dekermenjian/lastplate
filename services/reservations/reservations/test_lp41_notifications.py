"""LP-41: in-app notifications.

One test class per "Done when" line of the Jira card, plus its "Tests to
write": one notification per event, the reminder sent once, and only the
owner sees their notifications.
"""

import uuid
from datetime import timedelta

from django.utils import timezone

from . import timers
from .models import Actor, Notification, NotificationKind, Order, Status
from .notifications import REMINDER_MINUTES, notify, on_status_change, remind_orders_close_to_deadline
from .orders import InvalidStatusChange, change_status
from .test_lp19_checkout import CUSTOMER_ID, token
from .test_lp25_timers import TimerTestCase
from .test_lp78_my_orders import OTHER_CUSTOMER, MyOrdersApiTestCase, make_order

URL = "/api/notifications"
READ_URL = "/api/notifications/read"


def deadline_in(order, minutes, seconds=0):
    """Move the order's pick-up-by to `minutes` from now."""
    Order.objects.filter(pk=order.pk).update(pick_up_by=timezone.now() + timedelta(minutes=minutes, seconds=seconds))
    order.refresh_from_db()
    return order


class EventNotificationTests(TimerTestCase):
    """Done when: the customer gets a notification when the restaurant cancels
    (with the reason) and when an order expires."""

    def test_the_restaurant_cancelling_notifies_the_customer_with_the_reason(self):
        order = self.place()

        change_status(order, Status.CANCELLED, actor=Actor.SHOP, reason="Food ran out")

        notification = Notification.objects.get()
        self.assertEqual((notification.kind, notification.customer_user_id, notification.order_id),
                         (NotificationKind.SHOP_CANCELLED, CUSTOMER_ID, order.pk))
        self.assertIn("cancelled your order", notification.message)
        self.assertIn("Food ran out", notification.message)
        self.assertIsNone(notification.read_at)

    def test_the_restaurants_name_is_used_when_the_order_saved_it(self):
        order = self.place()
        Order.objects.filter(pk=order.pk).update(shop_name="Cedar Bakery")

        change_status(order, Status.CANCELLED, actor=Actor.SHOP, reason="Closing early")

        self.assertEqual(Notification.objects.get().message,
                         "Cedar Bakery cancelled your order. Reason: Closing early.")

    def test_an_order_that_expires_notifies_the_customer(self):
        order = self.place()

        timers.expire_due_orders(now=order.pick_up_by + timedelta(minutes=1))

        notification = Notification.objects.get()
        self.assertEqual((notification.kind, notification.customer_user_id), (NotificationKind.EXPIRED, CUSTOMER_ID))
        self.assertIn("expired", notification.message)

    def test_the_customer_cancelling_their_own_order_sends_nothing(self):
        order = self.place()

        change_status(order, Status.CANCELLED, actor=Actor.CUSTOMER, reason="Cancelled by the customer")

        self.assertEqual(Notification.objects.count(), 0)

    def test_paying_sends_nothing(self):
        order = self.place()

        change_status(order, Status.COLLECTED, actor=Actor.SHOP)

        self.assertEqual(Notification.objects.count(), 0)

    def test_a_change_that_is_refused_leaves_no_notification(self):
        order = self.place()
        change_status(order, Status.COLLECTED, actor=Actor.SHOP)

        with self.assertRaises(InvalidStatusChange):
            change_status(order, Status.CANCELLED, actor=Actor.SHOP, reason="Other")

        self.assertEqual(Notification.objects.count(), 0)

    def test_an_order_the_customer_never_saw_sends_nothing(self):
        order = make_order(status=Status.PROCESSING)

        change_status(order, Status.EXPIRED, actor=Actor.SYSTEM)

        self.assertEqual(Notification.objects.count(), 0)


class SentOnceTests(TimerTestCase):
    """Done when: the same notification is never sent twice. Tests: one
    notification per event."""

    def test_running_the_timer_again_does_not_notify_again(self):
        order = self.place()
        later = order.pick_up_by + timedelta(minutes=1)

        timers.expire_due_orders(now=later)
        timers.expire_due_orders(now=later)
        timers.run_timers(now=later)

        self.assertEqual(Notification.objects.filter(kind=NotificationKind.EXPIRED).count(), 1)

    def test_the_same_event_reported_twice_makes_one_notification(self):
        order = self.place()
        order = change_status(order, Status.CANCELLED, actor=Actor.SHOP, reason="Other")

        on_status_change(order, Status.CONFIRMED, Status.CANCELLED, Actor.SHOP, "Other", timezone.now())

        self.assertEqual(Notification.objects.count(), 1)

    def test_notify_creates_one_row_per_order_and_kind(self):
        order = self.place()

        first = notify(order, NotificationKind.EXPIRED, "one")
        second = notify(order, NotificationKind.EXPIRED, "two")

        self.assertEqual((first, second), (True, False))
        self.assertEqual(Notification.objects.get().message, "one")

    def test_each_event_of_two_orders_has_its_own_notification(self):
        first, second = self.place(), self.place()

        change_status(first, Status.CANCELLED, actor=Actor.SHOP, reason="Other")
        timers.expire_due_orders(now=second.pick_up_by + timedelta(minutes=1))

        self.assertEqual(sorted(Notification.objects.values_list("order_id", "kind")),
                         sorted([(first.pk, NotificationKind.SHOP_CANCELLED),
                                 (second.pk, NotificationKind.EXPIRED)]))


class ReminderTests(TimerTestCase):
    """Done when: the customer gets a notification when there are 10 minutes
    left to pick up. Tests: reminder sent once."""

    def test_ten_minutes_before_pick_up_by_the_customer_is_reminded(self):
        order = deadline_in(self.place(), REMINDER_MINUTES, seconds=-30)

        self.assertEqual(remind_orders_close_to_deadline(), 1)

        notification = Notification.objects.get()
        self.assertEqual((notification.kind, notification.order_id, notification.customer_user_id),
                         (NotificationKind.TEN_MINUTES_LEFT, order.pk, CUSTOMER_ID))
        self.assertIn("10 minutes left", notification.message)
        self.assertIn("Pick up by", notification.message)

    def test_the_reminder_is_sent_once_however_often_the_timer_runs(self):
        deadline_in(self.place(), 8)

        for _ in range(3):
            timers.expire_due_orders()
            remind_orders_close_to_deadline()
        timers.run_timers()

        self.assertEqual(Notification.objects.filter(kind=NotificationKind.TEN_MINUTES_LEFT).count(), 1)

    def test_the_timer_sends_it(self):
        deadline_in(self.place(), 8)

        timers.expire_due_orders()

        self.assertEqual(Notification.objects.get().kind, NotificationKind.TEN_MINUTES_LEFT)
        self.assertIn("8 minutes left", Notification.objects.get().message)

    def test_no_reminder_while_there_is_more_than_ten_minutes(self):
        deadline_in(self.place(), REMINDER_MINUTES, seconds=30)

        self.assertEqual(remind_orders_close_to_deadline(), 0)
        self.assertEqual(Notification.objects.count(), 0)

    def test_no_reminder_after_pick_up_by_only_the_expiry_notice(self):
        order = deadline_in(self.place(), -1)

        self.assertEqual(remind_orders_close_to_deadline(), 0)
        timers.expire_due_orders()

        self.assertEqual(list(Notification.objects.values_list("kind", flat=True)), [NotificationKind.EXPIRED])
        order.refresh_from_db()
        self.assertEqual(order.status, Status.EXPIRED)

    def test_no_reminder_for_orders_that_are_not_waiting_for_pick_up(self):
        paid = deadline_in(self.place(), 5)
        change_status(paid, Status.COLLECTED, actor=Actor.SHOP)
        cancelled = deadline_in(self.place(), 5)
        change_status(cancelled, Status.CANCELLED, actor=Actor.CUSTOMER, reason="Cancelled by the customer")

        self.assertEqual(remind_orders_close_to_deadline(), 0)

    def test_a_reminder_then_the_expiry_are_two_different_notifications(self):
        order = deadline_in(self.place(), 5)
        remind_orders_close_to_deadline()

        timers.expire_due_orders(now=order.pick_up_by + timedelta(minutes=1))

        self.assertEqual(sorted(Notification.objects.values_list("kind", flat=True)),
                         sorted([NotificationKind.TEN_MINUTES_LEFT, NotificationKind.EXPIRED]))


class NotificationApiTests(MyOrdersApiTestCase):
    """Done when: a bell with an unread count. Tests: only the owner sees
    their notifications."""

    def setUp(self):
        super().setUp()
        self.mine = make_order()
        self.theirs = make_order(customer=OTHER_CUSTOMER)
        notify(self.mine, NotificationKind.EXPIRED, "Mine expired.", timezone.now() - timedelta(minutes=5))
        notify(self.mine, NotificationKind.SHOP_CANCELLED, "Mine cancelled.")
        notify(self.theirs, NotificationKind.EXPIRED, "Theirs expired.")

    def listed(self, client=None):
        response = (client or self.client).get(URL)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_the_customer_sees_only_their_own_notifications_newest_first(self):
        body = self.listed()

        self.assertEqual([n["message"] for n in body["notifications"]], ["Mine cancelled.", "Mine expired."])
        self.assertEqual(body["unread"], 2)
        first = body["notifications"][0]
        self.assertEqual((first["kind"], first["read"], first["reservation_ref"]),
                         ("SHOP_CANCELLED", False, str(self.mine.reservation_ref)))

        other = self.listed(self.as_user(OTHER_CUSTOMER))
        self.assertEqual([n["message"] for n in other["notifications"]], ["Theirs expired."])
        self.assertEqual(other["unread"], 1)

    def test_the_bell_can_ask_for_just_the_count(self):
        response = self.client.get(URL, {"count_only": "1"})

        self.assertEqual(response.json(), {"unread": 2})

    def test_marking_one_read_lowers_the_count(self):
        ref = self.listed()["notifications"][0]["ref"]

        response = self.client.post(READ_URL, {"refs": [ref]}, format="json")

        self.assertEqual(response.json(), {"marked": 1, "unread": 1})
        body = self.listed()
        self.assertEqual([n["read"] for n in body["notifications"]], [True, False])

    def test_marking_all_read(self):
        response = self.client.post(READ_URL, {}, format="json")

        self.assertEqual(response.json(), {"marked": 2, "unread": 0})

    def test_someone_elses_notification_cannot_be_marked_read(self):
        theirs = Notification.objects.get(customer_user_id=OTHER_CUSTOMER)

        response = self.client.post(READ_URL, {"refs": [str(theirs.ref)]}, format="json")

        self.assertEqual(response.json()["marked"], 0)
        theirs.refresh_from_db()
        self.assertIsNone(theirs.read_at)

    def test_marking_all_never_touches_someone_elses(self):
        self.client.post(READ_URL, {}, format="json")

        self.assertEqual(Notification.objects.filter(customer_user_id=OTHER_CUSTOMER, read_at__isnull=True).count(), 1)

    def test_bad_references_are_refused(self):
        for body in ({"refs": "x"}, {"refs": ["not-a-uuid"]}):
            with self.subTest(body=body):
                self.assertEqual(self.client.post(READ_URL, body, format="json").status_code, 400)

    def test_an_unknown_reference_marks_nothing(self):
        response = self.client.post(READ_URL, {"refs": [str(uuid.uuid4())]}, format="json")

        self.assertEqual(response.json(), {"marked": 0, "unread": 2})

    def test_only_signed_in_customers_may_use_it(self):
        from rest_framework.test import APIClient

        self.assertEqual(APIClient().get(URL).status_code, 401)
        self.assertEqual(APIClient().post(READ_URL, {}, format="json").status_code, 401)
        operator = APIClient()
        operator.credentials(HTTP_AUTHORIZATION="Bearer " + token(user_id=71, role="SHOP_OPERATOR"))
        self.assertEqual(operator.get(URL).status_code, 403)
        self.assertEqual(operator.post(READ_URL, {}, format="json").status_code, 403)

    def test_reading_sends_a_due_reminder(self):
        deadline_in(self.mine, 7)

        body = self.listed()

        self.assertIn(NotificationKind.TEN_MINUTES_LEFT, [n["kind"] for n in body["notifications"]])
        self.assertEqual(body["unread"], 3)
        self.assertEqual(len(self.listed()["notifications"]), 3)  # reading again adds none

    def test_at_most_fifty_are_listed(self):
        for _ in range(55):
            notify(make_order(), NotificationKind.EXPIRED, "More.")

        self.assertEqual(len(self.listed()["notifications"]), 50)
