"""LP-41: the bell with an unread count, and the notifications page.

Notifications come from the Reservations API (faked here): it creates them
and only ever answers with the signed-in customer's own.
"""

import uuid

from django.urls import reverse

from .services import ServiceUnavailable
from .test_accounts import patch_service
from .test_lp56_login_signup import CUSTOMER, OPERATOR
from .test_lp62_cart import CartTestCase


def notification(message="Your order expired.", read=False, when="2026-11-19T17:15:00+00:00", **changes):
    item = {"ref": str(uuid.uuid4()), "kind": "EXPIRED", "message": message, "created_at": when, "read": read,
            "reservation_ref": str(uuid.uuid4())}
    item.update(changes)
    return item


def answer(items, unread=None):
    unread = sum(1 for n in items if not n["read"]) if unread is None else unread
    return 200, {"unread": unread, "notifications": items}


class BellTests(CartTestCase):
    """Done when: a bell with an unread count (in the customer bar)."""

    def page(self):
        with patch_service("my_orders", return_value=(200, {"orders": []})):
            return self.client.get(reverse("my-orders"))

    def test_signed_in_customers_see_the_bell_in_their_bar(self):
        self.sign_in_as(CUSTOMER)

        response = self.page()

        self.assertContains(response, "data-notifications-badge")
        self.assertContains(response, f'data-url="{reverse("notifications-count")}"')
        self.assertContains(response, f'href="{reverse("notifications")}"')
        self.assertContains(response, "js/notifications-badge.js")

    def test_visitors_do_not_get_the_bell(self):
        # Restaurants have no customer bar at all (test_lp18_account_side).
        response = self.client.get(reverse("login"))

        self.assertNotContains(response, "data-notifications-badge")
        self.assertNotContains(response, "notifications-badge.js")

    def test_the_count_comes_from_the_unread_notifications(self):
        self.sign_in_as(CUSTOMER)
        with patch_service("notifications", return_value=(200, {"unread": 3})) as call:
            response = self.client.get(reverse("notifications-count"))

        self.assertEqual(response.json(), {"unread": 3})
        self.assertTrue(call.call_args.args[1])  # count_only

    def test_the_count_is_unavailable_when_reservations_is_down_or_answers_oddly(self):
        self.sign_in_as(CUSTOMER)
        for kwargs in ({"side_effect": ServiceUnavailable("reservations")},
                       {"return_value": (500, {})}, {"return_value": (200, {"unread": "many"})},
                       {"return_value": (200, {"unread": -1})}):
            with self.subTest(kwargs=kwargs), patch_service("notifications", **kwargs):
                self.assertEqual(self.client.get(reverse("notifications-count")).status_code, 503)

    def test_only_customers_may_ask_for_the_count(self):
        self.assertEqual(self.client.get(reverse("notifications-count")).status_code, 302)
        self.sign_in_as(OPERATOR)
        with patch_service("notifications", side_effect=AssertionError("not for restaurants")):
            self.assertEqual(self.client.get(reverse("notifications-count")).status_code, 302)


class NotificationsPageTests(CartTestCase):
    def setUp(self):
        self.sign_in_as(CUSTOMER)

    def open(self, items, mark=(200, {"marked": 0, "unread": 0})):
        with patch_service("notifications", return_value=answer(items)), \
                patch_service("mark_notifications_read", return_value=mark) as marked:
            return self.client.get(reverse("notifications")), marked

    def test_the_messages_are_listed_with_the_time_in_lebanon(self):
        items = [notification("Cedar Bakery cancelled your order. Reason: Food ran out.", when="2026-11-19T17:15:00+00:00"),
                 notification("Your order expired.", read=True)]

        response, _ = self.open(items)

        self.assertContains(response, "Cedar Bakery cancelled your order. Reason: Food ran out.")
        self.assertContains(response, "Your order expired.")
        self.assertContains(response, "Thu 19 Nov, 19:15")  # 17:15 UTC is 19:15 in Beirut
        self.assertContains(response, reverse("order-detail", args=[items[0]["reservation_ref"]]))

    def test_unread_ones_are_highlighted_and_then_marked_read(self):
        unread, read = notification("New one."), notification("Old one.", read=True)

        response, marked = self.open([unread, read])

        self.assertContains(response, "notification--unread", count=1)
        marked.assert_called_once()
        self.assertEqual(marked.call_args.args[1], [unread["ref"]])

    def test_nothing_is_marked_when_everything_was_already_read(self):
        _, marked = self.open([notification(read=True)])

        marked.assert_not_called()

    def test_the_page_still_shows_when_marking_read_fails(self):
        with patch_service("notifications", return_value=answer([notification("Still here.")])), \
                patch_service("mark_notifications_read", side_effect=ServiceUnavailable("reservations")):
            response = self.client.get(reverse("notifications"))

        self.assertContains(response, "Still here.")

    def test_no_notifications_says_so(self):
        response, _ = self.open([])

        self.assertContains(response, "Nothing yet.")

    def test_a_service_problem_says_so_instead_of_an_empty_list(self):
        for kwargs in ({"side_effect": ServiceUnavailable("reservations")}, {"return_value": (500, {})},
                       {"return_value": (200, {"unread": 0})}):
            with self.subTest(kwargs=kwargs), patch_service("notifications", **kwargs):
                response = self.client.get(reverse("notifications"))
                self.assertEqual(response.status_code, 503)
                self.assertContains(response, "load your notifications right now", status_code=503)
                self.assertNotContains(response, "Nothing yet.", status_code=503)

    def test_visitors_are_sent_to_log_in_and_restaurants_away(self):
        self.client = self.client_class()
        self.assertEqual(self.client.get(reverse("notifications")).status_code, 302)
        self.sign_in_as(OPERATOR)
        with patch_service("notifications", side_effect=AssertionError("not for restaurants")):
            self.assertEqual(self.client.get(reverse("notifications")).status_code, 302)

    def test_a_message_is_escaped(self):
        response, _ = self.open([notification("<script>alert(1)</script>")])

        self.assertNotContains(response, "<script>alert(1)</script>")
