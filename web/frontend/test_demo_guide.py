from django.test import TestCase, override_settings
from django.urls import reverse


class DemoGuideTests(TestCase):
    @override_settings(DEBUG=True, MARKETPLACE_PUBLIC_URL="http://127.0.0.1:8002")
    def test_lists_every_sprint_1_story_in_development(self):
        response = self.client.get(reverse("demo-guide"))

        self.assertEqual(response.status_code, 200)
        for key in ("LP-7", "LP-8", "LP-9", "LP-10", "LP-11", "LP-12", "LP-13", "LP-14", "LP-15"):
            self.assertContains(response, key)
        self.assertContains(response, "http://127.0.0.1:8002/admin/marketplace/shop/")

    @override_settings(DEBUG=False)
    def test_does_not_exist_outside_development(self):
        self.assertEqual(self.client.get(reverse("demo-guide")).status_code, 404)
