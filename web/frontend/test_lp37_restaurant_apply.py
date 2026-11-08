"""LP-37: a restaurant fills in the three-step request and waits for review."""

import os
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from . import apply_views
from .services import ServiceUnavailable
from .test_accounts import SHOP, WebAuthTestCase, patch_service

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64

INFO = {"name": "Cedar Bakery", "category": "Bakery", "public_contact": "+961 1 234 567"}
LOCATION = {"address": "12 Bliss Street, Beirut", "area": "Hamra"}

NEW_SHOP = {**SHOP, "approval_status": "PENDING", "application_sent": False}
SENT_SHOP = {**SHOP, "name": "Cedar Bakery", "approval_status": "PENDING", "application_sent": True}
APPROVED_SHOP = {**SENT_SHOP, "approval_status": "APPROVED", "address": "12 Bliss Street, Beirut"}
REJECTED_SHOP = {**SENT_SHOP, "approval_status": "REJECTED", "rejection_reason": "We could not reach you."}


def step_url(step):
    return reverse("restaurant-apply", kwargs={"step": step})


def image(name="cover.jpg", content=JPEG, content_type="image/jpeg"):
    return SimpleUploadedFile(name, content, content_type=content_type)


class ApplyTestCase(WebAuthTestCase):
    def setUp(self):
        self.held_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.held_dir.cleanup)
        held = override_settings(APPLICATION_UPLOAD_DIR=self.held_dir.name)
        held.enable()
        self.addCleanup(held.disable)
        self.sign_in_session()
        shop = patch_service("my_shop", return_value=(404, {}))
        self.my_shop = shop.start()
        self.addCleanup(shop.stop)

    def post(self, step, data, **extra):
        return self.client.post(step_url(step), data, **extra)

    def finish_steps_1_and_2(self):
        self.post(1, INFO)
        self.post(2, LOCATION)

    def held_files(self):
        return os.listdir(self.held_dir.name)


class SignUpLeadsToTheRequestTests(ApplyTestCase):
    def test_restaurant_sign_up_opens_step_1(self):
        self.client.cookies.clear()
        account = {"id": 7, "email": "owner@example.test", "display_name": "Owner", "role": "SHOP_OPERATOR"}
        with patch_service("register", return_value=(201, {"access": "a", "refresh": "r", "user": account})):
            response = self.client.post(reverse("signup"), {
                "account_type": "restaurant", "display_name": "Owner",
                "email": "owner@example.test", "password": "Local-test-only!582Orbit",
            })
        self.assertRedirects(response, reverse("restaurant-apply"), fetch_redirect_response=False)

    def test_the_old_setup_address_opens_the_request(self):
        self.assertRedirects(self.client.get(reverse("finish-setup")), reverse("restaurant-apply"),
                             fetch_redirect_response=False)

    def test_step_1_shows_progress_categories_and_continue(self):
        response = self.client.get(reverse("restaurant-apply"))

        self.assertContains(response, "Register your restaurant")
        self.assertContains(response, "Step 1 of 3 · Restaurant info")
        self.assertContains(response, 'aria-valuenow="1"')
        for category in ("Bakery", "Café", "Restaurant", "Sushi", "Pastry", "Grocery", "Other"):
            self.assertContains(response, f'<option value="{category}"')
        self.assertContains(response, ">Continue</button>")
        self.assertNotContains(response, 'name="back"')
        self.assertNotContains(response, 'style="')


class StepValidationTests(ApplyTestCase):
    def test_step_1_requires_valid_restaurant_info(self):
        for field, value, message in (
            ("name", "", "This field is required."),
            ("category", "", "Choose a category."),
            ("category", "Pizza", "Choose a category from the list."),
            ("public_contact", "call me", "Enter a phone number"),
            ("public_contact", "123", "7 to 15 digits"),
        ):
            with self.subTest(field=field, value=value):
                response = self.post(1, {**INFO, field: value})
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, message)

    def test_step_1_keeps_what_was_typed_when_it_has_an_error(self):
        response = self.post(1, {**INFO, "public_contact": "123"})
        self.assertContains(response, 'value="Cedar Bakery"')
        self.assertContains(response, '<option value="Bakery" selected>')

    def test_step_2_requires_an_address_and_an_area_from_the_list(self):
        self.post(1, INFO)
        for data, message in (
            ({"address": ""}, "This field is required."),
            ({"area": ""}, "Choose your area from the list."),
            # LP-65: typed text is not an area, only a place from the list.
            ({"area": "Hamra street"}, "Choose your area from the list."),
        ):
            with self.subTest(data=data):
                response = self.post(2, {**LOCATION, **data})
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, message)

    def test_steps_cannot_be_skipped(self):
        self.assertRedirects(self.client.get(step_url(3)), step_url(1), fetch_redirect_response=False)
        self.post(1, INFO)
        self.assertRedirects(self.client.get(step_url(3)), step_url(2), fetch_redirect_response=False)
        self.assertEqual(self.client.get(step_url(4)).status_code, 404)

    def test_step_3_requires_a_cover_photo_and_the_conditions_tick(self):
        self.finish_steps_1_and_2()

        with patch_service("submit_application") as submit:
            response = self.post(3, {})

        self.assertContains(response, "Add a cover photo of your restaurant.")
        self.assertContains(response, "Tick the box to accept LastPlate&#x27;s conditions for restaurants.")
        submit.assert_not_called()

    def test_step_3_refuses_other_image_types(self):
        self.finish_steps_1_and_2()
        bad_files = (
            image("cover.gif", b"GIF89a" + b"0" * 20, "image/gif"),
            image("cover.jpg", b"not an image at all", "image/jpeg"),
            image("cover.png", JPEG, "image/png"),
        )
        for bad in bad_files:
            with self.subTest(file=bad.name), patch_service("submit_application") as submit:
                response = self.post(3, {"photo": bad, "accept_conditions": "on"})
                self.assertContains(response, "Choose a JPEG or PNG image.")
                submit.assert_not_called()
        self.assertEqual(self.held_files(), [])

    def test_a_valid_photo_is_kept_when_the_tick_is_missing(self):
        self.finish_steps_1_and_2()

        response = self.post(3, {"photo": image()})
        self.assertContains(response, "Kept: <strong>cover.jpg</strong>")
        self.assertEqual(len(self.held_files()), 1)

        with patch_service("submit_application", return_value=(201, SENT_SHOP)) as submit:
            response = self.post(3, {"accept_conditions": "on"})

        self.assertRedirects(response, reverse("request-status"), fetch_redirect_response=False)
        files = submit.call_args.args[2]
        self.assertEqual(files["photo"].name, "cover.jpg")
        self.assertEqual(files["photo"].read(), JPEG)
        self.assertEqual(self.held_files(), [])


class BackAndForthTests(ApplyTestCase):
    def test_going_back_keeps_every_answer(self):
        self.post(1, INFO)
        response = self.post(2, {**LOCATION, "address": "12 Bliss", "back": "1"})
        self.assertRedirects(response, step_url(1), fetch_redirect_response=False)

        step_1 = self.client.get(step_url(1))
        self.assertContains(step_1, 'value="Cedar Bakery"')
        self.assertContains(step_1, 'value="+961 1 234 567"')

        self.post(1, INFO)
        step_2 = self.client.get(step_url(2))
        self.assertContains(step_2, 'value="12 Bliss"')
        self.assertContains(step_2, 'value="Hamra"')
        self.assertContains(step_2, 'name="back"')

    def test_back_from_step_3_keeps_the_tick_and_photo(self):
        self.finish_steps_1_and_2()
        self.post(3, {"photo": image(), "accept_conditions": "on", "back": "1"})

        response = self.client.get(step_url(3))
        self.assertContains(response, "Kept: <strong>cover.jpg</strong>")
        self.assertContains(response, 'name="accept_conditions" required id="id_accept_conditions" checked')
        self.assertContains(response, ">Send request</button>")

    def test_an_earlier_name_only_setup_fills_in_step_1(self):
        self.my_shop.return_value = (200, {**NEW_SHOP, "name": "Cedar", "public_contact": "+961 1 111 222"})
        response = self.client.get(step_url(1))
        self.assertContains(response, 'value="Cedar"')
        self.assertContains(response, 'value="+961 1 111 222"')


class SendRequestTests(ApplyTestCase):
    def test_sending_passes_every_answer_and_both_images(self):
        self.post(1, INFO)
        # LP-65: there are no map boxes any more; coordinates sent anyway are ignored.
        self.post(2, {**LOCATION, "latitude": "33.896944", "longitude": "35.478333"})

        with patch_service("submit_application", return_value=(201, SENT_SHOP)) as submit:
            response = self.post(3, {"photo": image(), "logo": image("logo.png", PNG, "image/png"),
                                     "accept_conditions": "on"})

        self.assertRedirects(response, reverse("request-status"), fetch_redirect_response=False)
        access, fields, files = submit.call_args.args
        self.assertEqual(access, "access-token-1")
        self.assertEqual(fields, {
            "name": "Cedar Bakery", "category": "Bakery", "public_contact": "+961 1 234 567",
            "address": "12 Bliss Street, Beirut", "area": "Hamra", "accept_conditions": "true",
        })
        self.assertEqual(sorted(files), ["logo", "photo"])
        self.assertNotIn(apply_views.APPLICATION_KEY, self.client.session)

    def test_marketplace_refusing_the_image_asks_for_another(self):
        self.finish_steps_1_and_2()
        refusal = {"photo": ["Only JPEG and PNG images are allowed."]}
        with patch_service("submit_application", return_value=(400, refusal)):
            response = self.post(3, {"photo": image(), "accept_conditions": "on"})

        self.assertContains(response, "Only JPEG and PNG images are allowed.")
        self.assertNotContains(response, "Kept:")
        self.assertEqual(self.held_files(), [])

    def test_marketplace_refusing_an_earlier_answer_reopens_that_step(self):
        self.finish_steps_1_and_2()
        with patch_service("submit_application", return_value=(400, {"public_contact": ["Enter a real phone number."]})):
            response = self.post(3, {"photo": image(), "accept_conditions": "on"})

        self.assertContains(response, "Restaurant phone: Enter a real phone number.")
        self.assertRedirects(self.client.get(step_url(3)), step_url(1), fetch_redirect_response=False)

    def test_an_unreachable_service_keeps_the_answers(self):
        self.finish_steps_1_and_2()
        with patch_service("submit_application", side_effect=ServiceUnavailable("marketplace")):
            response = self.post(3, {"photo": image(), "accept_conditions": "on"})

        self.assertContains(response, "Your answers are kept")
        self.assertContains(response, "Kept: <strong>cover.jpg</strong>")

    def test_a_reviewed_restaurant_is_not_sent_again(self):
        self.finish_steps_1_and_2()
        with patch_service("submit_application", return_value=(409, {"detail": "reviewed"})):
            response = self.post(3, {"photo": image(), "accept_conditions": "on"})
        self.assertRedirects(response, reverse("workspace"), fetch_redirect_response=False)


class AccessBeforeAndAfterApprovalTests(ApplyTestCase):
    OFFER_PAGES = (
        ("get", reverse("create-listing")),
        ("get", reverse("edit-listing", kwargs={"listing_id": 5})),
        ("post", reverse("publish-listing", kwargs={"listing_id": 5})),
        ("post", reverse("adjust-stock", kwargs={"listing_id": 5})),
    )

    def open_offer_pages(self, shop_response):
        self.my_shop.return_value = shop_response
        with patch_service("create_listing") as create, patch_service("publish_listing") as publish, \
                patch_service("adjust_stock") as adjust, patch_service("edit_listing") as edit:
            responses = [getattr(self.client, method)(url) for method, url in self.OFFER_PAGES]
        for call in (create, publish, adjust, edit):
            call.assert_not_called()
        return responses

    def test_pending_owner_sees_the_request_and_no_offer_pages(self):
        self.my_shop.return_value = (200, SENT_SHOP)

        self.assertRedirects(self.client.get(reverse("workspace")), reverse("request-status"),
                             fetch_redirect_response=False)
        page = self.client.get(reverse("request-status"))
        self.assertContains(page, "Request sent")
        self.assertContains(page, "The LastPlate team is reviewing Cedar Bakery.")
        self.assertContains(page, "Request sent · conditions accepted")
        self.assertContains(page, "Log out")
        self.assertRedirects(self.client.get(step_url(1)), reverse("request-status"), fetch_redirect_response=False)

        for response in self.open_offer_pages((200, SENT_SHOP)):
            self.assertRedirects(response, reverse("request-status"), fetch_redirect_response=False)

    def test_rejected_owner_sees_the_reason_and_no_offer_pages(self):
        self.my_shop.return_value = (200, REJECTED_SHOP)

        page = self.client.get(reverse("request-status"))
        self.assertContains(page, "Request not approved")
        self.assertContains(page, "We could not reach you.")
        self.assertRedirects(self.client.get(reverse("workspace")), reverse("request-status"),
                             fetch_redirect_response=False)
        for response in self.open_offer_pages((200, REJECTED_SHOP)):
            self.assertRedirects(response, reverse("request-status"), fetch_redirect_response=False)

    def test_owner_who_has_not_finished_the_request_is_sent_back_to_it(self):
        for shop_response in ((404, {}), (200, NEW_SHOP)):
            with self.subTest(shop=shop_response[0]):
                self.my_shop.return_value = shop_response
                self.assertRedirects(self.client.get(reverse("workspace")), reverse("restaurant-apply"),
                                     fetch_redirect_response=False)
                for response in self.open_offer_pages(shop_response):
                    self.assertRedirects(response, reverse("restaurant-apply"), fetch_redirect_response=False)

    def test_offer_pages_fail_closed_when_the_shop_service_is_down(self):
        self.my_shop.side_effect = ServiceUnavailable("marketplace")
        with patch_service("create_listing") as create:
            response = self.client.get(reverse("create-listing"))
        self.assertRedirects(response, reverse("workspace"), fetch_redirect_response=False)
        create.assert_not_called()

    def test_approved_owner_lands_on_the_workspace_and_can_open_offer_pages(self):
        self.my_shop.return_value = (200, APPROVED_SHOP)
        self.client.cookies.clear()
        account = {"id": 7, "email": "owner@example.test", "display_name": "Owner",
                   "role": "SHOP_OPERATOR", "is_staff": False}
        with patch_service("sign_in", return_value=(200, {"access": "a", "refresh": "r"})), \
                patch_service("account", return_value=(200, account)):
            login = self.client.post(reverse("login"), {"email": "owner@example.test", "password": "x"})
        self.assertRedirects(login, reverse("workspace"), fetch_redirect_response=False)

        with patch_service("my_listings", return_value=(200, [])):
            self.assertEqual(self.client.get(reverse("workspace")).status_code, 200)
        page = self.client.get(reverse("create-listing"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "12 Bliss Street, Beirut")
        self.assertEqual(self.my_shop.call_count, 2)  # one per page, not one per check
        self.assertRedirects(self.client.get(reverse("request-status")), reverse("workspace"),
                             fetch_redirect_response=False)
