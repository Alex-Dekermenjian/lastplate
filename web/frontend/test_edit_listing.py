
"""Tests for editing food listing drafts (LP-10 and LP-11)."""

from django.urls import reverse

from .test_accounts import ApprovedShopMixin, WebAuthTestCase, patch_service


DRAFT_LISTING = {
    "id": 42,
    "title": "Pastry Box",
    "description": "Fresh pastries",
    "category": "Bakery",
    "status": "DRAFT",
    "original_price": "10.00",
    "discounted_price": "5.00",
    "total_quantity": 5,
    "pickup_address": "12 Bliss Street, Beirut",
    "pickup_start": None,
    "pickup_end": None,
    "ingredients": "Flour",
    "allergens": "Wheat",
    "photo_display": None,
}


class EditListingTests(ApprovedShopMixin, WebAuthTestCase):

    def setUp(self):
        self.url = reverse("edit-listing", args=[42])
        self.sign_in_session()

    def test_edit_page_shows_existing_details(self):
        with patch_service(
            "my_listings",
            return_value=(200, [DRAFT_LISTING]),
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Edit your food offer")
        self.assertContains(response, 'value="Pastry Box"')
        self.assertContains(response, "Save changes")

    def test_successful_edit_updates_existing_draft(self):
        posted = {
            "title": "Updated Pastry Box",
            "description": "More pastries",
            "category": "Bakery",
            "original_price": "10.00",
            "discounted_price": "4.00",
            "total_quantity": "3",
            "pickup_address": "12 Bliss Street, Beirut",
            "ingredients": "Flour",
            "allergens": "Wheat",
        }

        with patch_service(
            "my_listings",
            return_value=(200, [DRAFT_LISTING]),
        ), patch_service(
            "edit_listing",
            return_value=(200, {"id": 42, "status": "DRAFT"}),
        ) as edit_api:
            response = self.client.post(self.url, posted)

        self.assertRedirects(
            response,
            reverse("workspace"),
            fetch_redirect_response=False,
        )

        edit_api.assert_called_once()
        args = edit_api.call_args.args

        self.assertEqual(args[0], "access-token-1")
        self.assertEqual(args[1], 42)
        self.assertEqual(args[2]["title"], "Updated Pastry Box")
        self.assertEqual(args[2]["discounted_price"], "4.00")

    def test_other_shops_listing_cannot_be_edited(self):
        with patch_service(
            "my_listings",
            return_value=(200, []),
        ), patch_service("edit_listing") as edit_api:
            response = self.client.post(
                self.url,
                {"title": "Unauthorized change"},
            )

        self.assertEqual(response.status_code, 404)
        edit_api.assert_not_called()

    def test_published_listing_cannot_be_edited(self):
        published = {
            **DRAFT_LISTING,
            "status": "PUBLISHED",
        }

        with patch_service(
            "my_listings",
            return_value=(200, [published]),
        ), patch_service("edit_listing") as edit_api:
            response = self.client.post(
                self.url,
                {"title": "Unauthorized change"},
            )

        self.assertEqual(response.status_code, 404)
        edit_api.assert_not_called()

    def test_validation_error_preserves_input(self):
        with patch_service(
            "my_listings",
            return_value=(200, [DRAFT_LISTING]),
        ), patch_service(
            "edit_listing",
            return_value=(
                400,
                {"discounted_price": ["Must be below original price."]},
            ),
        ):
            response = self.client.post(
                self.url,
                {
                    "title": "Pastry Box",
                    "discounted_price": "15.00",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response,
            "Must be below original price.",
            status_code=400,
        )
        self.assertContains(
            response,
            'value="15.00"',
            status_code=400,
        )

from django.core.files.uploadedfile import SimpleUploadedFile


class EditListingPhotoTests(ApprovedShopMixin, WebAuthTestCase):

    def setUp(self):
        self.url = reverse("edit-listing", args=[42])
        self.sign_in_session()

    def test_replacing_photo_on_existing_draft(self):
        photo = SimpleUploadedFile(
            "new-pastries.png",
            b"\x89PNG\r\n\x1a\n" + b"test-image-data",
            content_type="image/png",
        )

        with patch_service(
            "my_listings",
            return_value=(200, [DRAFT_LISTING]),
        ), patch_service(
            "edit_listing",
            return_value=(200, {"id": 42, "status": "DRAFT"}),
        ) as edit_api, patch_service(
            "upload_listing_photo",
            return_value=(200, {"id": 42}),
        ) as upload_api:

            response = self.client.post(
                self.url,
                {
                    "title": "Pastry Box",
                    "photo": photo,
                },
            )

        self.assertRedirects(
            response,
            reverse("workspace"),
            fetch_redirect_response=False,
        )

        edit_api.assert_called_once()
        upload_api.assert_called_once()

        args = upload_api.call_args.args
        self.assertEqual(args[0], "access-token-1")
        self.assertEqual(args[1], 42)
        self.assertEqual(args[2].name, "new-pastries.png")

    def test_invalid_replacement_does_not_update_listing(self):
        invalid_photo = SimpleUploadedFile(
            "notes.txt",
            b"not an image",
            content_type="text/plain",
        )

        with patch_service(
            "my_listings",
            return_value=(200, [DRAFT_LISTING]),
        ), patch_service("edit_listing") as edit_api, patch_service(
            "upload_listing_photo",
        ) as upload_api:

            response = self.client.post(
                self.url,
                {
                    "title": "Pastry Box",
                    "photo": invalid_photo,
                },
            )

        self.assertEqual(response.status_code, 400)
        edit_api.assert_not_called()
        upload_api.assert_not_called()
        self.assertContains(
            response,
            "Upload a JPEG or PNG photo.",
            status_code=400,
        )
