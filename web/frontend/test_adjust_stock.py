"""LP-12: Adjusting a published offer's stock from the shop workspace."""

from unittest import mock

from django.urls import reverse

from . import services
from .services import ServiceUnavailable
from .test_accounts import WebAuthTestCase, patch_service
from .test_shop_profile import COMPLETE_SHOP


PUBLISHED_LISTING = {
    "id": 42,
    "title": "Croissant Box",
    "status": "PUBLISHED",
    "photo_display": None,
    "ingredients_display": "Unknown",
    "allergens_display": "Unknown",
    "total_quantity": 7,
    "free_quantity": 4,
}


def adjusted(free_quantity):
    return (
        201,
        {
            "adjustment": {"id": 1},
            "listing": {**PUBLISHED_LISTING, "free_quantity": free_quantity},
        },
    )


class AdjustStockViewTests(WebAuthTestCase):

    def setUp(self):
        self.url = reverse("adjust-stock", args=[42])
        self.sign_in_session()

    def post_and_follow(self, data):
        """Post the form, then load the workspace to read the message."""
        with patch_service(
            "my_shop",
            return_value=(200, COMPLETE_SHOP),
        ), patch_service(
            "my_listings",
            return_value=(200, [PUBLISHED_LISTING]),
        ):
            return self.client.post(self.url, data, follow=True)

    def test_get_request_is_not_allowed(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 405)

    def test_signed_out_user_is_redirected(self):
        self.client = self.client_class()  # no session

        with patch_service("adjust_stock") as adjust_api:
            response = self.client.post(
                self.url,
                {"quantity_change": "3", "reason": "Baked more"},
            )

        self.assertEqual(response.status_code, 302)
        adjust_api.assert_not_called()

    def test_adding_stock(self):
        with patch_service(
            "adjust_stock",
            return_value=adjusted(7),
        ) as adjust_api:
            response = self.client.post(
                self.url,
                {"quantity_change": "3", "reason": "  Baked more  "},
            )

        self.assertRedirects(
            response,
            reverse("workspace"),
            fetch_redirect_response=False,
        )
        adjust_api.assert_called_once_with(
            "access-token-1", 42, 3, "Baked more"
        )

    def test_success_message_shows_new_available_quantity(self):
        with patch_service("adjust_stock", return_value=adjusted(7)):
            response = self.post_and_follow(
                {"quantity_change": "3", "reason": "Baked more"}
            )

        self.assertContains(response, "Added 3 units. 7 now available.")

    def test_removing_stock(self):
        with patch_service(
            "adjust_stock",
            return_value=adjusted(3),
        ) as adjust_api:
            response = self.post_and_follow(
                {"quantity_change": "-1", "reason": "One dropped"}
            )

        adjust_api.assert_called_once_with(
            "access-token-1", 42, -1, "One dropped"
        )
        self.assertContains(response, "Removed 1 unit. 3 now available.")

    # ---------- Checked before calling the API ----------

    def test_form_errors_do_not_call_the_api(self):
        cases = (
            ({"quantity_change": "", "reason": "x"}, "Enter a whole number"),
            ({"quantity_change": "1.5", "reason": "x"}, "Enter a whole number"),
            ({"quantity_change": "abc", "reason": "x"}, "Enter a whole number"),
            ({"quantity_change": "0", "reason": "x"}, "other than zero"),
            ({"quantity_change": "2", "reason": "   "}, "Give a reason"),
            ({"quantity_change": "2"}, "Give a reason"),
        )

        for data, message in cases:
            with self.subTest(data=data):
                with patch_service("adjust_stock") as adjust_api:
                    response = self.post_and_follow(data)

                adjust_api.assert_not_called()
                self.assertContains(response, message)

    # ---------- Refusals from the Marketplace API ----------

    def test_api_refusals_show_clear_messages(self):
        cases = (
            (400, "below_zero", "can&#x27;t remove more units"),
            (400, "below_committed", "already reserved"),
            (409, "not_published", "Only published offers"),
            (409, "pickup_ended", "pickup window has ended"),
            (403, "not_owner", "your own offers"),
            (400, "invalid", "Enter a whole number to add or remove"),
            (418, "something_new", "was unsuccessful"),
        )

        for status, code, message in cases:
            with self.subTest(code=code):
                with patch_service(
                    "adjust_stock",
                    return_value=(status, {"code": code, "detail": "x"}),
                ):
                    response = self.post_and_follow(
                        {"quantity_change": "-9", "reason": "Dropped"}
                    )

                self.assertContains(response, message)
                self.assertNotContains(response, "now available")

    def test_unreachable_service_warns_not_to_repeat(self):
        with patch_service(
            "adjust_stock",
            side_effect=ServiceUnavailable("marketplace"),
        ):
            response = self.post_and_follow(
                {"quantity_change": "2", "reason": "Baked more"}
            )

        self.assertContains(response, "couldn&#x27;t confirm")
        self.assertContains(response, "before trying again")


class WorkspaceStockTests(WebAuthTestCase):

    def setUp(self):
        self.sign_in_session()

    def get_workspace(self, listings):
        with patch_service(
            "my_shop",
            return_value=(200, {**COMPLETE_SHOP, "approval_status": "APPROVED"}),
        ), patch_service(
            "my_listings",
            return_value=(200, listings),
        ):
            return self.client.get(reverse("workspace"))

    def test_published_listing_shows_free_quantity_and_form(self):
        response = self.get_workspace([PUBLISHED_LISTING])

        self.assertContains(response, "Available quantity:")
        self.assertContains(response, "of 7 in stock")
        self.assertContains(response, "Adjust stock")
        self.assertContains(
            response,
            f'action="{reverse("adjust-stock", args=[42])}"',
        )
        self.assertContains(response, 'name="quantity_change"')
        self.assertContains(response, 'name="reason"')
        self.assertContains(response, "csrfmiddlewaretoken")

    def test_draft_listing_has_no_stock_form(self):
        draft = {**PUBLISHED_LISTING, "status": "DRAFT"}

        response = self.get_workspace([draft])

        self.assertNotContains(response, "Adjust stock")
        self.assertContains(response, "Quantity:")


class StockServiceClientTests(WebAuthTestCase):

    def test_adjust_stock_posts_change_and_reason(self):
        with mock.patch.object(services, "_call", return_value=(201, {})) as call:
            services.adjust_stock("access-token-1", 42, -2, "Dropped")

        call.assert_called_once_with(
            "marketplace",
            "POST",
            "/api/listings/42/stock-adjustments",
            {"quantity_change": -2, "reason": "Dropped"},
            token="access-token-1",
        )

    def test_stock_history_gets_listing_history(self):
        with mock.patch.object(services, "_call", return_value=(200, [])) as call:
            services.stock_history("access-token-1", 42)

        call.assert_called_once_with(
            "marketplace",
            "GET",
            "/api/listings/42/stock-adjustments",
            token="access-token-1",
        )
