from unittest import mock

from django.test import Client
from django.urls import reverse

from . import services
from .services import ServiceUnavailable
from .test_accounts import ACCOUNT, SHOP, WebAuthTestCase, patch_service

PROFILE = {"name": "Cedar Crumb Bakery", "address": "12 Bliss Street, Beirut", "area": "Hamra",
           "public_contact": "+961 1 234 567"}
# LP-37: the workspace and offer pages are for approved restaurants.
COMPLETE_SHOP = {**SHOP, **PROFILE, "rejection_reason": "", "profile_complete": True,
                 "approval_status": "APPROVED", "application_sent": True}
NEW_SHOP = {**SHOP, "address": "", "area": "", "public_contact": "", "rejection_reason": "", "profile_complete": False}


class ShopProfilePageTests(WebAuthTestCase):
    url = reverse("shop-profile")

    def test_requires_sign_in_and_sends_no_store(self):
        response = self.client.get(self.url)
        self.assertRedirects(response, f"{reverse('login')}?next=%2Fworkspace%2Fprofile%2F", fetch_redirect_response=False)
        self.sign_in_session()
        with patch_service("my_shop", return_value=(200, COMPLETE_SHOP)):
            self.assertIn("no-store", self.client.get(self.url)["Cache-Control"])

    def test_form_is_prefilled_from_the_operators_own_shop(self):
        self.sign_in_session()
        with patch_service("my_shop", return_value=(200, COMPLETE_SHOP)) as my_shop:
            response = self.client.get(self.url)
        my_shop.assert_called_once_with("access-token-1")
        for value in PROFILE.values():
            self.assertContains(response, f'value="{value}"')
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertContains(response, "Approved")

    def test_login_email_is_never_used_or_shown(self):
        self.sign_in_session()
        with patch_service("my_shop", return_value=(200, {**NEW_SHOP, "approval_status": "APPROVED"})), \
                patch_service("my_listings", return_value=(200, [])):
            for url in (self.url, reverse("workspace")):
                with self.subTest(url=url):
                    response = self.client.get(url)
                    self.assertNotContains(response, ACCOUNT["email"])
        with patch_service("my_shop", return_value=(200, NEW_SHOP)):
            form_html = self.client.get(self.url).content.decode()
        self.assertIn('name="public_contact"', form_html)
        self.assertNotIn(f'value="{ACCOUNT["email"]}"', form_html)

    def test_saving_sends_only_profile_fields_and_returns_to_workspace(self):
        self.sign_in_session()
        posted = {**PROFILE, "approval_status": "APPROVED", "operator_user_id": "99", "rejection_reason": "x"}
        with patch_service("my_shop", return_value=(200, {**NEW_SHOP, "approval_status": "APPROVED"})), \
                patch_service("update_shop", return_value=(200, COMPLETE_SHOP)) as update_shop, \
                patch_service("my_listings", return_value=(200, [])):
            response = self.client.post(self.url, posted, follow=True)
        update_shop.assert_called_once_with("access-token-1", PROFILE)
        self.assertRedirects(response, reverse("workspace"))
        self.assertContains(response, "Your shop profile has been saved.")

    def test_local_validation_errors_keep_input(self):
        self.sign_in_session()
        with patch_service("my_shop", return_value=(200, NEW_SHOP)), patch_service("update_shop") as update_shop:
            response = self.client.post(self.url, {**PROFILE, "area": "   ", "address": "x" * 256})
        update_shop.assert_not_called()
        self.assertContains(response, "Choose your area from the list.")
        self.assertContains(response, "at most 255 characters")
        self.assertContains(response, 'value="Cedar Crumb Bakery"')

    def test_phone_field_is_a_tel_input_and_short_values_are_caught_locally(self):
        self.sign_in_session()
        with patch_service("my_shop", return_value=(200, NEW_SHOP)):
            page = self.client.get(self.url)
        self.assertContains(page, 'type="tel"')
        self.assertContains(page, "Shop phone number")
        with patch_service("my_shop", return_value=(200, NEW_SHOP)), patch_service("update_shop") as update_shop:
            response = self.client.post(self.url, {**PROFILE, "name": "A", "address": "abc", "area": "H"})
        update_shop.assert_not_called()
        self.assertContains(response, "at least 2 characters", count=1)
        self.assertContains(response, "at least 5 characters")
        self.assertContains(response, "Choose your area from the list.")

    def test_unusable_phone_rejected_by_marketplace_is_shown(self):
        self.sign_in_session()
        error = {"public_contact": ["Enter a full phone number with 7 to 15 digits, for example +961 1 234 567."]}
        with patch_service("my_shop", return_value=(200, NEW_SHOP)), \
                patch_service("update_shop", return_value=(400, error)) as update_shop:
            response = self.client.post(self.url, {**PROFILE, "public_contact": "666"})
        update_shop.assert_called_once()
        self.assertContains(response, "Enter a full phone number with 7 to 15 digits")
        self.assertContains(response, 'value="666"')

    def test_marketplace_validation_errors_are_shown_on_fields(self):
        self.sign_in_session()
        with patch_service("my_shop", return_value=(200, NEW_SHOP)), \
                patch_service("update_shop", return_value=(400, {"public_contact": ["Enter a valid contact."]})):
            response = self.client.post(self.url, PROFILE)
        self.assertContains(response, "Enter a valid contact.")
        self.assertContains(response, 'value="12 Bliss Street, Beirut"')

    def test_unavailable_service_is_reported_and_input_kept(self):
        self.sign_in_session()
        with patch_service("my_shop", side_effect=ServiceUnavailable("marketplace")), \
                patch_service("update_shop", side_effect=ServiceUnavailable("marketplace")):
            response = self.client.post(self.url, PROFILE)
            page = self.client.get(self.url)
        self.assertContains(response, "your changes were not saved")
        self.assertContains(response, 'value="Hamra"')
        self.assertContains(page, "profile can't be loaded")

    def test_operator_without_shop_is_sent_to_the_request(self):
        self.sign_in_session()
        with patch_service("my_shop", return_value=(404, {})):
            self.assertRedirects(self.client.get(self.url), reverse("restaurant-apply"), fetch_redirect_response=False)

    def test_post_without_csrf_token_is_rejected(self):
        client = Client(enforce_csrf_checks=True)
        with patch_service("update_shop") as update_shop:
            self.assertEqual(client.post(self.url, PROFILE).status_code, 403)
        update_shop.assert_not_called()


class WorkspaceProfileTests(WebAuthTestCase):
    def test_shows_profile_details_and_completion_prompt(self):
        self.sign_in_session()
        with patch_service("my_shop", return_value=(200, {**NEW_SHOP, "approval_status": "APPROVED"})):
            response = self.client.get(reverse("workspace"))
        self.assertContains(response, "Not set", count=3)
        self.assertContains(response, "Complete your pickup address, area and shop phone number")
        self.assertContains(response, reverse("shop-profile"))
        with patch_service("my_shop", return_value=(200, COMPLETE_SHOP)):
            response = self.client.get(reverse("workspace"))
        self.assertContains(response, "12 Bliss Street, Beirut")
        self.assertNotContains(response, "Complete your pickup address")

    def test_rejection_reason_is_shown_only_for_rejected_shops(self):
        self.sign_in_session()
        rejected = {**COMPLETE_SHOP, "approval_status": "REJECTED", "rejection_reason": "We could not confirm you represent this shop."}
        # LP-37: a rejected owner reads the reason on their request page.
        with patch_service("my_shop", return_value=(200, rejected)):
            response = self.client.get(reverse("request-status"))
        self.assertContains(response, "Request not approved")
        self.assertContains(response, "We could not confirm you represent this shop.")
        with patch_service("my_shop", return_value=(200, {**rejected, "rejection_reason": ""})):
            self.assertNotContains(self.client.get(reverse("request-status")), "<h2>Reason</h2>")
        with patch_service("my_shop", return_value=(200, {**COMPLETE_SHOP, "rejection_reason": "stale text"})):
            self.assertNotContains(self.client.get(reverse("workspace")), "stale text")

    def test_rejection_reason_is_escaped(self):
        self.sign_in_session()
        rejected = {**COMPLETE_SHOP, "approval_status": "REJECTED", "rejection_reason": "<script>alert(1)</script>"}
        with patch_service("my_shop", return_value=(200, rejected)):
            response = self.client.get(reverse("request-status"))
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertContains(response, "&lt;script&gt;")


class ServiceClientProfileTests(WebAuthTestCase):
    def test_update_shop_patches_own_shop_endpoint(self):
        with mock.patch.object(services, "_call", return_value=(200, {})) as call:
            services.update_shop("access-token-1", PROFILE)
        call.assert_called_once_with("marketplace", "PATCH", "/api/shops/me/", PROFILE, token="access-token-1")
