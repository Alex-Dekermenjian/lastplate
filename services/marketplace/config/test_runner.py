"""Runs every Marketplace test with uploads in a temporary folder.

MEDIA_ROOT is a persistent volume in Docker, so a test that saves a photo must never
write there. It also would not be writable in every environment a test runs in.
"""

import shutil
import tempfile

from django.test import override_settings
from django.test.runner import DiscoverRunner


class TemporaryMediaRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self._media_dir = tempfile.mkdtemp(prefix="lastplate-test-media-")
        self._media_override = override_settings(MEDIA_ROOT=self._media_dir)
        self._media_override.enable()

    def teardown_test_environment(self, **kwargs):
        self._media_override.disable()
        shutil.rmtree(self._media_dir, ignore_errors=True)
        super().teardown_test_environment(**kwargs)
