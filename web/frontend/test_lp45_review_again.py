"""LP-45: a rejected restaurant fixes its details and asks for review again."""

from django.urls import reverse

from .services import ServiceUnavailable
from .test_accounts import WebAuthTestCase, patch_service
from .test_lp37_restaurant_apply import REJECTED_SHOP, SENT_SHOP
from .test_lp57_console import ADMIN, SHOP, ConsoleTestCase

URL = reverse("request-review-again")


class RejectedOwnerPageTests(WebAuthTestCase):

    def setUp(self):
        self.sign_in_session()

    def page(self, shop):
        with patch_service("my_shop", return_value=(200, shop)):
            return self.client.get(reverse("request-status"))

    def test_the_reason_a_link_to_fix_and_the_button(self):
        response = self.page(REJECTED_SHOP)

        self.assertContains(response, "We could not reach you.")
        self.assertContains(response, f'href="{reverse("shop-profile")}"')
        self.assertContains(response, f'action="{URL}"')
        self.assertContains(response, 'name="note"')
        self.assertContains(response, "Request review again")

    def test_a_pending_owner_has_no_button(self):
        self.assertNotContains(self.page(SENT_SHOP), "Request review again")

    def test_after_asking_again_the_page_says_so(self):
        response = self.page({**SENT_SHOP, "review_requests": 1})

        self.assertContains(response, "Request sent again")


class ReviewAgainViewTests(WebAuthTestCase):

    def setUp(self):
        self.sign_in_session()

    def post(self, note="", **service):
        with patch_service("review_again", **service) as api:
            response = self.client.post(URL, {"note": note})
        return response, api

    def message(self, response):
        with patch_service("my_shop", return_value=(200, SENT_SHOP)):
            return self.client.get(response.url)

    def test_sent_again_with_the_note(self):
        response, api = self.post("  New phone number.  ", return_value=(200, {**SENT_SHOP, "review_requests": 1}))

        self.assertEqual(api.call_args.args[1], "New phone number.")
        self.assertRedirects(response, reverse("request-status"), fetch_redirect_response=False)
        self.assertContains(self.message(response), "Your request was sent again.")

    def test_already_pending_or_approved(self):
        response, _ = self.post(return_value=(409, {"code": "not_rejected"}))

        self.assertContains(self.message(response), "already being reviewed or approved")

    def test_unreachable_service_warns_not_to_repeat(self):
        response, _ = self.post(side_effect=ServiceUnavailable("marketplace"))

        self.assertContains(self.message(response), "couldn&#x27;t confirm whether your request was sent again")

    def test_a_too_long_note_is_not_sent(self):
        response, api = self.post("x" * 1001)

        api.assert_not_called()
        self.assertContains(self.message(response), "Keep your note to 1000 characters or fewer.")

    def test_only_post(self):
        with patch_service("review_again") as api:
            self.assertEqual(self.client.get(URL).status_code, 405)
        api.assert_not_called()


class ConsoleSentAgainTests(ConsoleTestCase):

    def setUp(self):
        self.sign_in_as(ADMIN)
        self.again = {**SHOP, "id": 4, "name": "Again Bakery", "review_requests": 2,
                      "last_rejection_reason": "Blurry photo.", "review_request_note": "New photo."}

    def test_the_pending_list_marks_and_can_filter_them(self):
        with patch_service("admin_shops", return_value=(200, [SHOP, self.again])):
            everyone = self.client.get(reverse("console-restaurants"))
            again = self.client.get(reverse("console-restaurants"), {"again": "1"})

        self.assertContains(everyone, "Cedar Crumb Bakery")
        self.assertContains(everyone, "Asked again ×2")
        self.assertContains(again, "Again Bakery")
        self.assertNotContains(again, "Cedar Crumb Bakery")
        self.assertContains(again, "Show all pending")

    def test_the_detail_shows_the_earlier_reason_and_the_note(self):
        with patch_service("admin_shop", return_value=(200, self.again)):
            response = self.client.get(reverse("console-restaurant", args=[4]))

        self.assertContains(response, "Asked for review again")
        self.assertContains(response, "(2 times)")
        self.assertContains(response, "Blurry photo.")
        self.assertContains(response, "New photo.")
