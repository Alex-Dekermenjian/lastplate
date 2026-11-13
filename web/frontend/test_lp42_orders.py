from django.urls import reverse

from .services import ServiceUnavailable
from .test_accounts import APPROVED_SHOP, WebAuthTestCase, patch_service
from .test_lp78_my_orders import make_order


class OwnerOrderPagesTests(WebAuthTestCase):
    def setUp(self):
        self.sign_in_session()
        shop = patch_service("my_shop", return_value=(200, APPROVED_SHOP))
        shop.start()
        self.addCleanup(shop.stop)
        self.body = {"orders": [{**make_order(), "customer_first_name": "Maya"}],
                     "branches": [{"id": 7, "name": "Hamra"}], "count": 1}
        patcher = patch_service("shop_orders", return_value=(200, self.body))
        self.api = patcher.start()
        self.addCleanup(patcher.stop)

    def test_render_fields_and_forward_only_allowed_filters(self):
        response = self.client.get(reverse("today-orders"), {"q": "Maya", "branch_id": "7", "shop_id": "99"})
        for text in ("Today's orders", "TM9AY3", "Maya", "2 × Glazed donuts", "$4.50", "min left",
                     "Hamra", 'name="q"', 'name="branch_id"', "js/countdown.js", "js/orders-badge.js"):
            self.assertContains(response, text)
        self.api.assert_called_once_with("access-token-1", {"q": "Maya", "branch_id": "7"})
        self.assert_no_secrets(response)
        self.assertNotContains(response, "Mark paid")
        self.assertIn("no-store", response["Cache-Control"])

    def test_badge_is_unfiltered_and_failure_is_not_zero(self):
        response = self.client.get(reverse("orders-badge"), {"q": "missing", "branch_id": 99})
        self.assertEqual(response.json(), {"count": 1})
        self.api.assert_called_once_with("access-token-1", {"count_only": "1"})
        self.api.side_effect = ServiceUnavailable("reservations")
        response = self.client.get(reverse("orders-badge"))
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("count", response.json())

    def test_badge_shared_on_other_owner_pages(self):
        with patch_service("my_shop", return_value=(200, APPROVED_SHOP)), \
                patch_service("my_listings", return_value=(200, [])), \
                patch_service("my_branches", return_value=(200, [])):
            for name in ("workspace", "shop-profile"):
                response = self.client.get(reverse(name))
                self.assertContains(response, "data-orders-badge")
                self.assertContains(response, reverse("orders-badge"))
                self.assertContains(response, "js/orders-badge.js")

    def test_empty_and_unavailable_states(self):
        self.body["orders"] = []
        self.assertContains(self.client.get(reverse("today-orders")), "No Reserved orders")
        self.api.side_effect = ServiceUnavailable("reservations")
        response = self.client.get(reverse("today-orders"))
        self.assertContains(response, "Can't load orders", status_code=503)
        self.assertContains(response, "Try again", status_code=503)

    def test_names_are_escaped_and_invalid_branch_is_not_silently_cleared(self):
        self.body["orders"][0]["customer_first_name"] = '<script>alert("name")</script>'
        response = self.client.get(reverse("today-orders"))
        self.assertNotContains(response, '<script>alert("name")</script>')
        self.assertContains(response, "&lt;script&gt;")
        self.api.return_value = (404, {"code": "not_found"})
        self.assertEqual(self.client.get(reverse("today-orders"), {"branch_id": 999}).status_code, 404)

    def test_malformed_service_responses_show_unavailable_instead_of_crashing(self):
        for changes in ({"orders": [None]}, {"branches": [None]}, {"count": -1},
                        {"orders": [{**make_order(), "lines": [None]}]},
                        {"orders": [{**make_order(), "lines": [{"quantity": "broken"}]}]}):
            with self.subTest(changes=changes):
                self.api.return_value = (200, {**self.body, **changes})
                self.assertContains(self.client.get(reverse("today-orders")), "Can't load orders", status_code=503)

    def test_token_refresh_stays_server_side(self):
        self.api.side_effect = [(401, {}), (200, self.body)]
        with patch_service("refresh", return_value=(200, {"access": "renewed-secret"})):
            response = self.client.get(reverse("today-orders"))
        self.assertContains(response, "Maya")
        self.assert_no_secrets(response, "renewed-secret")
        self.assertEqual(self.api.call_args.args[0], "renewed-secret")

    def test_unauthenticated_customers_and_admins_cannot_access(self):
        for role in ("CUSTOMER", "ADMIN", None):
            session = self.client.session
            if role:
                session["account"] = {"id": 7, "role": role}
            else:
                session.flush()
            session.save()
            for name in ("today-orders", "orders-badge"):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 302)
        self.api.assert_not_called()
