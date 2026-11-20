"""Demo clock: the moved time every service reads."""

import json
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase
from django.utils import timezone

from config import demo_clock


class DemoClockTests(SimpleTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.file = Path(folder.name) / "clock.json"
        self.enterContext(mock.patch.dict("os.environ", {"DEMO_CLOCK_FILE": str(self.file)}))
        self.forget()

    def forget(self):
        demo_clock._seen.update(mtime=None, offset=0)

    def write(self, text):
        self.file.write_text(text, encoding="utf-8")
        self.forget()

    def shift(self):
        return (demo_clock.demo_now() - demo_clock.real_now()).total_seconds()

    def test_without_a_file_it_is_the_real_time(self):
        self.assertEqual(demo_clock.offset_seconds(), 0)
        self.assertAlmostEqual(self.shift(), 0, delta=2)

    def test_the_offset_in_the_file_moves_now(self):
        self.write(json.dumps({"offset_seconds": 3600}))

        self.assertEqual(demo_clock.offset_seconds(), 3600)
        self.assertAlmostEqual(self.shift(), 3600, delta=2)

    def test_it_can_move_back_in_time(self):
        self.write(json.dumps({"offset_seconds": -86400}))

        self.assertAlmostEqual(self.shift(), -86400, delta=2)

    def test_a_new_value_is_picked_up_when_the_file_changes(self):
        self.write(json.dumps({"offset_seconds": 60}))
        self.assertEqual(demo_clock.offset_seconds(), 60)

        self.file.write_text(json.dumps({"offset_seconds": 7200}), encoding="utf-8")

        self.assertEqual(demo_clock.offset_seconds(), 7200)

    def test_a_broken_file_means_the_real_time(self):
        for text in ("", "not json", "{}", '{"offset_seconds": "soon"}', '{"offset_seconds": true}', "[1]"):
            with self.subTest(text=text):
                self.write(text)
                self.assertEqual(demo_clock.offset_seconds(), 0)

    def test_a_huge_offset_is_limited(self):
        self.write(json.dumps({"offset_seconds": 10**12}))

        self.assertEqual(demo_clock.offset_seconds(), demo_clock.MAX_OFFSET_SECONDS)

    def test_without_the_setting_the_file_is_ignored(self):
        self.write(json.dumps({"offset_seconds": 3600}))

        with mock.patch.dict("os.environ", {"DEMO_CLOCK_FILE": ""}):
            self.assertEqual(demo_clock.offset_seconds(), 0)

    def test_install_replaces_now_but_never_while_testing(self):
        with mock.patch.object(timezone, "now", timezone.now):
            with mock.patch.object(demo_clock, "testing", return_value=True):
                demo_clock.install()
                self.assertIsNot(timezone.now, demo_clock.demo_now)
            with mock.patch.object(demo_clock, "testing", return_value=False):
                demo_clock.install()
                self.assertIs(timezone.now, demo_clock.demo_now)
                demo_clock.install()  # twice is fine
                self.assertIs(timezone.now, demo_clock.demo_now)

    def test_install_does_nothing_without_the_setting(self):
        with mock.patch.dict("os.environ", {"DEMO_CLOCK_FILE": ""}), \
                mock.patch.object(demo_clock, "testing", return_value=False), \
                mock.patch.object(timezone, "now", timezone.now):
            demo_clock.install()
            self.assertIsNot(timezone.now, demo_clock.demo_now)
