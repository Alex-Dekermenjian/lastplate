from unittest.mock import patch, MagicMock
from urllib.error import HTTPError
from io import BytesIO
from email.message import Message

from django.test import Client
from django.urls import reverse

from . import services
from .services import ServiceUnavailable
from .test_accounts import WebAuthTestCase, patch_service, APPROVED_SHOP


def sales_body():
    return {
        "filters": {"from": "2026-11-16", "to": "2026-11-16", "branch": 7},
        "branches": [{"id": 7, "name": "Hamra"}],
        "summary": {"orders": 51, "paid_orders": 2, "cash_collected": "12.30",
                    "cash_expected": "4.50", "items_saved": 6, "expired_orders": 3},
        "pagination": {"page": 1, "page_size": 50, "total": 51},
        "orders": [{"reservation_ref": "test-order", "branch_name": "Hamra", "status_label": "Paid",
                    "pick_up_by": "2026-11-16T18:00:00+02:00", "collected_at": "2026-11-16T17:30:00+02:00",
                    "quantity": 6, "order_total": "12.30", "cash_collected": "12.30", "cash_expected": "0.00"}],
    }


class SalesPagesTests(WebAuthTestCase):
    def setUp(self):
        self.sign_in_session()
        shop = patch_service("my_shop", return_value=(200, APPROVED_SHOP))
        shop.start()
        self.addCleanup(shop.stop)

    def test_history_filters_summary_table_pagination_and_no_secrets(self):
        with patch_service("shop_sales", return_value=(200, sales_body())) as call:
            response = self.client.get(reverse("sales-history"), {"from": "2026-11-16", "to": "2026-11-16",
                                       "branch": "7", "shop_id": 99})
        call.assert_called_once_with("access-token-1", {"from": "2026-11-16", "to": "2026-11-16", "branch": "7", "page": "1"})
        for text in ("Sales history", "Total orders", "USD 12.30", "Items saved", "Expired orders", "Hamra",
                     "test-order", "Paid at", "Export CSV", "page=2"):
            self.assertContains(response, text)
        self.assert_no_secrets(response)
        self.assertIn("no-store", response["Cache-Control"])

    def test_dashboard_cards_use_api_summary(self):
        with patch_service("shop_orders", return_value=(200, {"orders": [], "branches": [], "count": 0,
                                                             "summary": sales_body()["summary"]})) as call:
            response = self.client.get(reverse("today-orders"), {"branch_id": "7", "q": "name", "status": "COLLECTED"})
        self.assertEqual(call.call_args.args[1], {"branch_id": "7", "q": "name", "status": "COLLECTED", "include_summary": "1"})
        for text in ("Paid orders", "USD 12.30", "USD 4.50", "Expired orders", "Sales history"):
            self.assertContains(response, text)

    def test_export_forwards_only_filters_and_preserves_bytes(self):
        with patch_service("export_sales", return_value=(200, b"branch,currency\r\nHamra,USD\r\n")) as call:
            response = self.client.get(reverse("sales-export"), {"from": "2026-11-16", "to": "2026-11-16", "branch": "7", "page": 9, "shop_id": 99})
        call.assert_called_once_with("access-token-1", {"from": "2026-11-16", "to": "2026-11-16", "branch": "7"})
        self.assertEqual(response.content, b"branch,currency\r\nHamra,USD\r\n")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn("no-store", response["Cache-Control"])

    def test_export_refresh_and_errors_are_not_downloads(self):
        with patch_service("export_sales", side_effect=[(401, {}), (200, b"currency\r\nUSD\r\n")]) as call, \
                patch_service("refresh", return_value=(200, {"access": "renewed-secret"})):
            response = self.client.get(reverse("sales-export"))
        self.assertEqual(call.call_args.args[0], "renewed-secret")
        self.assert_no_secrets(response, "renewed-secret")
        for name in ("sales-history", "sales-export"):
            service = "shop_sales" if name == "sales-history" else "export_sales"
            for status in (400, 404):
                with patch_service(service, return_value=(status, {"detail": "Invalid filter"})):
                    response = self.client.get(reverse(name))
                self.assertContains(response, "Invalid filter", status_code=status)
                self.assertNotIn("Content-Disposition", response)
            with patch_service(service, side_effect=ServiceUnavailable()):
                self.assertEqual(self.client.get(reverse(name)).status_code, 503)

    def test_empty_malformed_and_escaped_history(self):
        body = sales_body()
        body["orders"][0]["branch_name"] = "<script>bad</script>"
        with patch_service("shop_sales", return_value=(200, body)):
            self.assertContains(self.client.get(reverse("sales-history")), "&lt;script&gt;")
        body["orders"] = []
        with patch_service("shop_sales", return_value=(200, body)):
            self.assertContains(self.client.get(reverse("sales-history")), "No orders match")
        with patch_service("shop_sales", return_value=(200, {})):
            self.assertEqual(self.client.get(reverse("sales-history")).status_code, 503)

    def test_roles_get_only_and_existing_payment_csrf(self):
        # Reporting is read-only GET; unsafe methods must not bypass CSRF.
        client = Client(enforce_csrf_checks=True)
        client.cookies = self.client.cookies
        for name in ("sales-history", "sales-export", "today-orders"):
            self.assertEqual(client.post(reverse(name)).status_code, 403)
            self.assertEqual(self.client.post(reverse(name)).status_code, 405)
        self.assertEqual(client.post(reverse("pickup-paid", args=["5a533422-899f-4538-961f-8453973f9efe"])).status_code, 403)
        for role in ("CUSTOMER", "ADMIN", None):
            session = self.client.session
            if role:
                session["account"] = {"id": 7, "role": role}
            else:
                session.flush()
            session.save()
            with patch_service("shop_sales") as call:
                for name in ("sales-history", "sales-export"):
                    self.assertEqual(self.client.get(reverse(name)).status_code, 302)
                call.assert_not_called()

    def test_csv_http_client_handles_csv_and_json_errors(self):
        upstream = MagicMock()
        upstream.status = 200
        upstream.headers = Message()
        upstream.headers["Content-Type"] = "text/csv; charset=utf-8"
        upstream.read.return_value = b"currency\r\nUSD\r\n"
        with patch("frontend.services.urlopen") as open_url:
            open_url.return_value.__enter__.return_value = upstream
            self.assertEqual(services.export_sales("secret", {"branch": 7}), (200, b"currency\r\nUSD\r\n"))
            request = open_url.call_args.args[0]
            self.assertEqual(request.get_header("Authorization"), "Bearer secret")
            self.assertTrue(request.full_url.endswith("/api/shop/sales/export?branch=7"))
            upstream.headers.replace_header("Content-Type", "text/html")
            with self.assertRaises(ServiceUnavailable):
                services.export_sales("secret")
        error = HTTPError("http://unused", 401, "Unauthorized", {}, BytesIO(b'{"code":"invalid_token"}'))
        with patch("frontend.services.urlopen", side_effect=error):
            self.assertEqual(services.export_sales("secret"), (401, {"code": "invalid_token"}))
