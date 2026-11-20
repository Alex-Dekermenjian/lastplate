"""Demo clock: the date and time box and the view that moves the clock."""

import json
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from config import demo_clock
from frontend import services


class DemoClockTestCase(TestCase):
    enabled = True

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.file = Path(folder.name) / "clock.json"
        self.enterContext(mock.patch.object(demo_clock, "enabled", return_value=self.enabled))
        self.enterContext(mock.patch.object(demo_clock, "path", return_value=str(self.file)))
        demo_clock._seen.update(mtime=None, offset=0)
        # Moving the clock back asks Reservations to delete later orders.
        self.rewinds = []
        self.rewind_answer = (200, {"orders_removed": 0, "allocations_removed": 0})

        def fake_rewind(since):
            self.rewinds.append(since)
            return self.rewind_answer

        self.enterContext(mock.patch.object(services, "rewind_orders", side_effect=fake_rewind))

    def messages_of(self, response, level):
        return [m.message for m in get_messages(response.wsgi_request) if m.level_tag == level]

    def offset(self):
        return json.loads(self.file.read_text(encoding="utf-8"))["offset_seconds"]

    def set_time(self, moment, **extra):
        text = timezone.localtime(moment).strftime("%Y-%m-%dT%H:%M:%S")
        return self.client.post(reverse("demo-clock"), {"moment": text, "action": "set", **extra})

    def errors(self, response):
        return [m.message for m in get_messages(response.wsgi_request) if m.level_tag == "error"]


class SetClockTests(DemoClockTestCase):
    def test_setting_a_later_time_stores_the_difference(self):
        response = self.set_time(timezone.now() + timedelta(hours=5))

        self.assertEqual(response.status_code, 302)
        self.assertAlmostEqual(self.offset(), 5 * 3600, delta=5)

    def test_setting_an_earlier_date_works_too(self):
        self.set_time(timezone.now() - timedelta(days=3))

        self.assertAlmostEqual(self.offset(), -3 * 86400, delta=5)

    def test_the_time_may_be_typed_without_seconds(self):
        moment = timezone.localtime(timezone.now() + timedelta(hours=2))
        self.client.post(reverse("demo-clock"), {"moment": moment.strftime("%Y-%m-%dT%H:%M"), "action": "set"})

        self.assertAlmostEqual(self.offset(), 2 * 3600, delta=65)

    def test_use_real_time_puts_the_offset_back_to_zero(self):
        self.set_time(timezone.now() + timedelta(hours=5))

        self.client.post(reverse("demo-clock"), {"action": "reset"})

        self.assertEqual(self.offset(), 0)

    def test_a_bad_time_changes_nothing_and_says_so(self):
        for text in ("", "tomorrow", "2026-13-40T99:00:00"):
            with self.subTest(text=text):
                self.client = self.client_class()  # a fresh session, so earlier errors don't pile up
                response = self.client.post(reverse("demo-clock"), {"moment": text, "action": "set"})

                self.assertFalse(self.file.exists())
                self.assertEqual(len(self.errors(response)), 1)

    def test_a_date_too_far_away_is_refused(self):
        response = self.client.post(reverse("demo-clock"), {"moment": "2999-01-01T00:00:00", "action": "set"})

        self.assertFalse(self.file.exists())
        self.assertEqual(len(self.errors(response)), 1)

    def test_it_goes_back_to_the_page_it_was_on(self):
        response = self.set_time(timezone.now(), next=reverse("browse-offers"))

        self.assertEqual(response["Location"], reverse("browse-offers"))

    def test_another_site_in_next_is_ignored(self):
        response = self.set_time(timezone.now(), next="http://evil.example/steal")

        self.assertEqual(response["Location"], reverse("home"))

    def test_only_post_is_allowed(self):
        self.assertEqual(self.client.get(reverse("demo-clock")).status_code, 405)


class MovingBackTests(DemoClockTestCase):
    def test_moving_forward_deletes_no_orders(self):
        self.set_time(timezone.now() + timedelta(hours=5))

        self.assertEqual(self.rewinds, [])

    def test_moving_back_asks_reservations_to_delete_orders_after_the_new_time(self):
        self.set_time(timezone.now() + timedelta(hours=5))
        target = timezone.now() + timedelta(hours=2)

        self.set_time(target)

        self.assertEqual(len(self.rewinds), 1)
        self.assertAlmostEqual(self.rewinds[0].timestamp(), target.timestamp(), delta=5)

    def test_going_back_to_real_time_does_it_too(self):
        self.set_time(timezone.now() + timedelta(hours=5))

        self.client.post(reverse("demo-clock"), {"action": "reset"})

        self.assertEqual(len(self.rewinds), 1)
        self.assertAlmostEqual(self.rewinds[0].timestamp(), timezone.now().timestamp(), delta=5)

    def test_it_says_how_many_orders_were_deleted(self):
        self.rewind_answer = (200, {"orders_removed": 2})
        response = self.set_time(timezone.now() - timedelta(hours=3))

        self.assertEqual(self.messages_of(response, "info"),
                         ["2 orders placed after this time were deleted and the food is back on sale."])

    def test_one_order_is_worded_in_the_singular(self):
        self.rewind_answer = (200, {"orders_removed": 1})
        response = self.set_time(timezone.now() - timedelta(hours=3))

        self.assertEqual(self.messages_of(response, "info"),
                         ["1 order placed after this time was deleted and the food is back on sale."])

    def test_no_message_when_nothing_was_deleted(self):
        response = self.set_time(timezone.now() - timedelta(hours=3))

        self.assertEqual(self.messages_of(response, "info"), [])

    def test_the_clock_is_still_set_when_the_orders_could_not_be_deleted(self):
        for answer in ((503, {}), None):
            with self.subTest(answer=answer):
                self.client = self.client_class()
                if answer is None:
                    services.rewind_orders.side_effect = services.ServiceUnavailable("reservations")
                else:
                    self.rewind_answer = answer
                response = self.set_time(timezone.now() - timedelta(hours=3))

                self.assertAlmostEqual(self.offset(), -3 * 3600, delta=5)
                self.assertEqual(len(self.messages_of(response, "warning")), 1)


class ClockBoxTests(DemoClockTestCase):
    def test_every_page_shows_the_date_and_time_box(self):
        response = self.client.get(reverse("login"))

        self.assertContains(response, "data-demo-clock")
        self.assertContains(response, 'type="datetime-local"')
        self.assertContains(response, "js/demo-clock.js")
        year = str(timezone.localtime(timezone.now()).year)
        self.assertContains(response, year)

    def test_a_moved_clock_is_labelled_demo_time(self):
        self.file.write_text(json.dumps({"offset_seconds": 3600}), encoding="utf-8")
        demo_clock._seen.update(mtime=None, offset=0)

        response = self.client.get(reverse("login"))

        self.assertContains(response, "Demo time")
        self.assertContains(response, 'data-offset-ms="3600000"')

    def test_the_real_time_is_labelled_time(self):
        response = self.client.get(reverse("login"))

        self.assertNotContains(response, "Demo time")


class DisabledTests(DemoClockTestCase):
    enabled = False

    def test_the_view_does_not_exist_when_the_clock_is_off(self):
        response = self.client.post(reverse("demo-clock"), {"moment": "2026-11-20T10:00:00", "action": "set"})

        self.assertEqual(response.status_code, 404)
        self.assertFalse(self.file.exists())

    def test_the_box_is_not_on_the_page_when_the_clock_is_off(self):
        response = self.client.get(reverse("login"))

        self.assertNotContains(response, "data-demo-clock")
