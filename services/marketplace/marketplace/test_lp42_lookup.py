from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import Branch
from .test_browse import make_shop
from .test_lp20_stock_api import TEST_KEYS, OLD_KEY, WEB_KEY, service_client


@override_settings(INTERNAL_SERVICE_KEYS=TEST_KEYS)
class OperatorLookupTests(TestCase):
    def test_lookup_returns_only_matching_shop_and_includes_removed_branches(self):
        shop = make_shop(operator_user_id=71)
        other = make_shop(operator_user_id=72)
        removed = Branch.objects.create(shop=shop, name="Old branch", removed_at=timezone.now())
        url = "/internal/shops/by-operator/71"
        body = service_client().get(url).json()
        self.assertEqual(body["id"], shop.id)
        self.assertEqual({b["id"] for b in body["branches"]}, set(shop.branches.values_list("id", flat=True)))
        self.assertIn(removed.id, [b["id"] for b in body["branches"]])
        self.assertNotIn(other.branches.first().id, [b["id"] for b in body["branches"]])
        self.assertEqual(service_client(key=OLD_KEY).get(url).status_code, 200)
        self.assertEqual(service_client().get("/internal/shops/by-operator/999").status_code, 404)

    def test_internal_credentials_required_and_other_callers_refused(self):
        url = "/internal/shops/by-operator/71"
        self.assertEqual(APIClient().get(url).status_code, 401)
        self.assertEqual(service_client(key="wrong").get(url).status_code, 403)
        self.assertEqual(service_client(name="web", key=WEB_KEY).get(url).status_code, 403)
        self.assertEqual(service_client().post(url).status_code, 405)
