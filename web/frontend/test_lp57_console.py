"""LP-57: the Admin role logs in to /console and reviews restaurants there."""

from urllib.parse import urlencode

from django.urls import reverse
from django.utils.html import escape

from . import account_views, tokens
from .services import ServiceUnavailable
from .test_accounts import PASSWORD, TOKENS, WebAuthTestCase, patch_service
from .test_lp14_permissions import NoServiceCalls
from .test_lp56_login_signup import CUSTOMER, OPERATOR

ADMIN = {"id": 99, "email": "admin@example.test", "display_name": "Lina", "role": "ADMIN", "is_staff": False}
SHOP = {
    "id": 3, "operator_user_id": 7, "name": "Cedar Crumb Bakery", "address": "12 Bliss Street, Beirut",
    "area": "Hamra", "public_contact": "+961 1 234 567", "profile_complete": True,
    "approval_status": "PENDING", "rejection_reason": "", "reviewed_by_user_id": None, "reviewed_at": None,
    "created_at": "2026-11-05T10:00:00Z", "updated_at": "2026-11-05T10:00:00Z",
}
SUMMARY = {"pending_shops": 2, "approved_shops": 5, "rejected_shops": 1, "live_offers": 7}


def console_pages():
    return [
        ("get", reverse("console")),
        ("get", reverse("console-restaurants")),
        ("get", reverse("console-restaurant", args=[3])),
        ("post", reverse("console-restaurant", args=[3])),
    ]


class ConsoleTestCase(WebAuthTestCase):
    def sign_in_as(self, account):
        session = self.client.session
        session[tokens.ACCESS_TOKEN_KEY] = TOKENS["access"]
        session[tokens.REFRESH_TOKEN_KEY] = TOKENS["refresh"]
        session[account_views.ACCOUNT_KEY] = {k: account[k] for k in ("id", "email", "display_name", "role")}
        session.save()


class AdminLoginTests(ConsoleTestCase):
    def test_admin_logs_in_and_lands_on_the_console(self):
        with patch_service("sign_in", return_value=(200, TOKENS)), \
                patch_service("account", return_value=(200, ADMIN)), \
                patch_service("sign_out") as sign_out:
            response = self.client.post(reverse("login"), {"email": ADMIN["email"], "password": PASSWORD})

        self.assertRedirects(response, reverse("console"), fetch_redirect_response=False)
        sign_out.assert_not_called()
        self.assertEqual(self.client.session[account_views.ACCOUNT_KEY]["role"], "ADMIN")

    def test_signup_page_never_offers_an_admin_account(self):
        response = self.client.get(reverse("signup"))
        self.assertNotContains(response, 'value="admin"')
        with patch_service("register") as register:
            self.client.post(reverse("signup"), {"account_type": "admin", "display_name": "X",
                                                 "email": "x@example.test", "password": PASSWORD})
        register.assert_not_called()


class ConsoleAccessTests(ConsoleTestCase):
    def test_signed_out_visitors_are_sent_to_login_and_back(self):
        for method, url in console_pages():
            with self.subTest(url=url), NoServiceCalls() as calls:
                response = getattr(self.client, method)(url)
                self.assertRedirects(
                    response, f"{reverse('login')}?{urlencode({'next': url})}", fetch_redirect_response=False
                )
                self.assertFalse(any(mock.called for mock in calls.mocks.values()))

    def test_customers_and_restaurants_cannot_open_the_console(self):
        for account, landing in ((CUSTOMER, reverse("home")), (OPERATOR, reverse("workspace"))):
            self.sign_in_as(account)
            for method, url in console_pages():
                with self.subTest(role=account["role"], url=url), NoServiceCalls() as calls:
                    response = getattr(self.client, method)(url, {"decision": "APPROVED", "identity_confirmed": "on"})
                    self.assertRedirects(response, landing, fetch_redirect_response=False)
                    self.assertFalse(any(mock.called for mock in calls.mocks.values()))

    def test_admins_cannot_open_customer_or_restaurant_pages(self):
        self.sign_in_as(ADMIN)
        for url in (reverse("my-account"), reverse("workspace")):
            with self.subTest(url=url):
                self.assertRedirects(self.client.get(url), reverse("console"), fetch_redirect_response=False)


class ConsolePagesTests(ConsoleTestCase):
    def setUp(self):
        self.sign_in_as(ADMIN)

    def test_home_shows_the_counts(self):
        with patch_service("admin_summary", return_value=(200, SUMMARY)) as summary:
            response = self.client.get(reverse("console"))

        summary.assert_called_once_with(TOKENS["access"])
        for text in ("Pending restaurants", "Approved restaurants", "Live offers", ">2<", ">5<", ">7<"):
            self.assertContains(response, text)

    def test_home_explains_when_marketplace_is_down(self):
        with patch_service("admin_summary", side_effect=ServiceUnavailable("marketplace")):
            response = self.client.get(reverse("console"))
        self.assertContains(response, "can't load the numbers")

    def test_restaurant_tabs(self):
        with patch_service("admin_shops", return_value=(200, [SHOP])) as shops:
            response = self.client.get(reverse("console-restaurants"))
            shops.assert_called_once_with(TOKENS["access"], "PENDING")
            self.assertContains(response, "Cedar Crumb Bakery")
            self.assertContains(response, reverse("console-restaurant", args=[3]))

            self.client.get(reverse("console-restaurants"), {"tab": "rejected"})
            shops.assert_called_with(TOKENS["access"], "REJECTED")
            self.client.get(reverse("console-restaurants"), {"tab": "../../etc"})
            shops.assert_called_with(TOKENS["access"], "PENDING")

    def test_restaurant_page_shows_the_full_profile_and_both_decisions(self):
        with patch_service("admin_shop", return_value=(200, SHOP)):
            response = self.client.get(reverse("console-restaurant", args=[3]))

        for text in ("Cedar Crumb Bakery", "12 Bliss Street, Beirut", "Hamra", "+961 1 234 567",
                     "Pending review", "Approve restaurant", "Reject restaurant",
                     "I confirmed the applicant represents this restaurant"):
            self.assertContains(response, text)

    def test_unknown_restaurant_is_404(self):
        with patch_service("admin_shop", return_value=(404, {"detail": "Shop not found."})):
            self.assertEqual(self.client.get(reverse("console-restaurant", args=[3])).status_code, 404)


class ConsoleDecisionTests(ConsoleTestCase):
    url = reverse("console-restaurant", args=[3])

    def setUp(self):
        self.sign_in_as(ADMIN)

    def decide(self, data, review=(200, {**SHOP, "approval_status": "APPROVED"})):
        with patch_service("admin_review_shop", return_value=review) as review_api, \
                patch_service("admin_shop", return_value=(200, SHOP)):
            response = self.client.post(self.url, data)
        return response, review_api

    def test_approve_sends_the_confirmation_and_shows_success(self):
        response, review = self.decide({"decision": "APPROVED", "identity_confirmed": "on"})

        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        review.assert_called_once_with(TOKENS["access"], 3, {"decision": "APPROVED", "identity_confirmed": True})

    def test_approve_without_the_tick_is_stopped_before_marketplace(self):
        response, review = self.decide({"decision": "APPROVED"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Confirm the applicant represents this restaurant")
        review.assert_not_called()

    def test_reject_sends_the_reason(self):
        response, review = self.decide(
            {"decision": "REJECTED", "reason": "We couldn't verify the owner."},
            review=(200, {**SHOP, "approval_status": "REJECTED"}),
        )

        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        review.assert_called_once_with(
            TOKENS["access"], 3, {"decision": "REJECTED", "reason": "We couldn't verify the owner."}
        )

    def test_reject_without_a_reason_is_stopped_before_marketplace(self):
        response, review = self.decide({"decision": "REJECTED", "reason": "   "})

        self.assertContains(response, "Give a reason")
        review.assert_not_called()

    def test_marketplace_rule_errors_are_shown(self):
        message = "This shop's profile is incomplete, so it cannot be approved."
        response, _ = self.decide(
            {"decision": "APPROVED", "identity_confirmed": "on"},
            review=(400, {"detail": "This decision isn't allowed.", "fields": {"approval_status": message}}),
        )
        self.assertContains(response, escape(message))

    def test_unknown_outcome_is_explained(self):
        with patch_service("admin_review_shop", side_effect=ServiceUnavailable("marketplace")), \
                patch_service("admin_shop", return_value=(200, SHOP)):
            response = self.client.post(self.url, {"decision": "REJECTED", "reason": "No."})
        self.assertContains(response, escape("can't tell whether your decision was saved"))
