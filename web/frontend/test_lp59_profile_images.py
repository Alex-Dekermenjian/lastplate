"""LP-59 (part): after approval, the owner can still change the category,
logo and cover photo from the shop profile page."""

from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from . import services
from .test_accounts import WebAuthTestCase, patch_service
from .test_shop_profile import PROFILE, SHOP

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
APPROVED = {**SHOP, "approval_status": "APPROVED", "category": "Bakery",
            "logo_display": "/media/shop_logos/old.png", "photo_display": "/media/shop_photos/old.jpg"}


def png(name="logo.png"):
    return SimpleUploadedFile(name, PNG, content_type="image/png")


@override_settings(DEBUG=True, MARKETPLACE_PUBLIC_URL="http://127.0.0.1:8002")
class ProfileImagesTests(WebAuthTestCase):
    url = reverse("shop-profile")

    def setUp(self):
        self.sign_in_session()

    def post(self, data, result=(200, APPROVED)):
        with patch_service("my_shop", return_value=(200, APPROVED)), \
                patch_service("update_shop", return_value=result) as update_shop:
            response = self.client.post(self.url, data)
        return response, update_shop

    def test_the_page_shows_the_current_logo_and_cover_and_can_upload(self):
        with patch_service("my_shop", return_value=(200, APPROVED)):
            page = self.client.get(self.url)

        self.assertContains(page, 'enctype="multipart/form-data"')
        self.assertContains(page, 'src="http://127.0.0.1:8002/media/shop_logos/old.png"')
        self.assertContains(page, 'src="http://127.0.0.1:8002/media/shop_photos/old.jpg"')
        for field in ('name="category"', 'name="logo"', 'name="photo"'):
            self.assertContains(page, field)
        self.assertContains(page, '<option value="Bakery" selected>Bakery</option>', html=True)

    def test_a_new_logo_is_sent_and_the_cover_is_kept(self):
        response, update_shop = self.post({**PROFILE, "logo": png()})

        self.assertRedirects(response, reverse("workspace"), fetch_redirect_response=False)
        _access, details, images = update_shop.call_args.args
        self.assertEqual(sorted(images), ["logo"])
        self.assertNotIn("logo", details)
        self.assertNotIn("photo", details)
        self.assertEqual(details["category"], "Bakery")

    def test_no_new_image_keeps_both(self):
        _response, update_shop = self.post(PROFILE)

        self.assertEqual(update_shop.call_args.args[2], {})

    def test_the_category_can_change_after_approval(self):
        _response, update_shop = self.post({**PROFILE, "category": "Café"})

        self.assertEqual(update_shop.call_args.args[1]["category"], "Café")

    def test_a_category_outside_the_list_is_refused(self):
        response, update_shop = self.post({**PROFILE, "category": "Nightclub"})

        update_shop.assert_not_called()
        self.assertContains(response, "Choose a category from the list.")

    def test_a_file_that_is_not_an_image_is_refused_before_sending(self):
        fake = SimpleUploadedFile("logo.png", b"GIF89a not a png", content_type="image/png")
        response, update_shop = self.post({**PROFILE, "logo": fake})

        update_shop.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors.get("logo"))

    def test_marketplace_refusing_the_image_is_shown_on_that_field(self):
        refusal = (400, {"photo": ["Only JPEG and PNG images are allowed."]})
        response, _ = self.post({**PROFILE, "photo": png("cover.png")}, result=refusal)

        self.assertContains(response, "Only JPEG and PNG images are allowed.")
        self.assertEqual(response.context["form"].errors["photo"], ["Only JPEG and PNG images are allowed."])


class UpdateShopServiceTests(WebAuthTestCase):
    def test_images_go_as_multipart_and_details_alone_as_json(self):
        with mock.patch.object(services, "_call_multipart", return_value=(200, {})) as multipart, \
                mock.patch.object(services, "_call", return_value=(200, {})) as json_call:
            services.update_shop("token", {"name": "Cedar"}, {"logo": png()})
            services.update_shop("token", {"name": "Cedar"})

        self.assertEqual(multipart.call_args.kwargs["files"].keys(), {"logo"})
        self.assertEqual(multipart.call_args.args[1], "PATCH")
        json_call.assert_called_once_with("marketplace", "PATCH", "/api/shops/me/", {"name": "Cedar"}, token="token")
