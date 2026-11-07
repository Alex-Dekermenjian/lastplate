
"""Tests for LP-11 photo uploads through the Web frontend."""

from io import BytesIO
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from . import services
from .test_accounts import ApprovedShopMixin, WebAuthTestCase, patch_service


class ListingPhotoTests(ApprovedShopMixin, WebAuthTestCase):

    def setUp(self):
        self.url = reverse("create-listing")
        self.sign_in_session()

    def make_photo(self):
        # A small PNG file for testing the Web-to-API request.
        return SimpleUploadedFile(
            "pastries.png",
            b"\x89PNG\r\n\x1a\n" + b"test-image-data",
            content_type="image/png",
        )

    def test_successful_photo_upload_after_draft_creation(self):
        with patch_service(
            "create_listing",
            return_value=(201, {"id": 42, "status": "DRAFT"}),
        ) as create_draft, patch_service(
            "upload_listing_photo",
            return_value=(200, {"id": 42, "photo_display": "/media/test.png"}),
        ) as upload_photo:

            response = self.client.post(
                self.url,
                {
                    "title": "Pastry Box",
                    "photo": self.make_photo(),
                },
            )

        self.assertRedirects(
            response,
            reverse("workspace"),
            fetch_redirect_response=False,
        )

        create_draft.assert_called_once()
        upload_photo.assert_called_once()

        args = upload_photo.call_args.args
        self.assertEqual(args[0], "access-token-1")
        self.assertEqual(args[1], 42)
        self.assertEqual(args[2].name, "pastries.png")

    def test_no_photo_skips_upload(self):
        with patch_service(
            "create_listing",
            return_value=(201, {"id": 42, "status": "DRAFT"}),
        ), patch_service("upload_listing_photo") as upload_photo:

            response = self.client.post(
                self.url,
                {"title": "Bread Box"},
            )

        self.assertEqual(response.status_code, 302)
        upload_photo.assert_not_called()

    def test_invalid_file_type_does_not_create_draft(self):
        invalid_file = SimpleUploadedFile(
            "notes.txt",
            b"not an image",
            content_type="text/plain",
        )

        with patch_service("create_listing") as create_draft:
            response = self.client.post(
                self.url,
                {
                    "title": "Pastry Box",
                    "photo": invalid_file,
                },
            )

        self.assertEqual(response.status_code, 400)
        create_draft.assert_not_called()
        self.assertContains(
            response,
            "Upload a JPEG or PNG photo.",
            status_code=400,
        )

    def test_photo_failure_preserves_created_draft(self):
        with patch_service(
            "create_listing",
            return_value=(201, {"id": 42, "status": "DRAFT"}),
        ), patch_service(
            "upload_listing_photo",
            return_value=(400, {"photo": ["Invalid image."]}),
        ):

            response = self.client.post(
                self.url,
                {
                    "title": "Pastry Box",
                    "photo": self.make_photo(),
                },
                follow=False,
            )

        self.assertRedirects(
            response,
            reverse("workspace"),
            fetch_redirect_response=False,
        )

    def test_multipart_request_uses_correct_endpoint(self):
        photo = self.make_photo()

        with mock.patch.object(
            services,
            "_call_multipart",
            return_value=(200, {"id": 42}),
        ) as call:
            services.upload_listing_photo(
                "access-token-1",
                42,
                photo,
            )

        call.assert_called_once_with(
            "marketplace",
            "POST",
            "/api/listings/42/photo",
            files={"photo": photo},
            token="access-token-1",
        )

