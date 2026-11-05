"""LP-13: Public pages for browsing food offers and viewing offer details."""

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils.html import escape

from .browse_views import UNAVAILABLE_MESSAGE as MESSAGE
from .services import ServiceUnavailable
from .test_accounts import patch_service


# Templates escape the apostrophe in "can't".
UNAVAILABLE = escape(MESSAGE)


def offer(**overrides):
    data = {
        "id": 42,
        "title": "Croissant Box",
        "description": "Fresh pastries",
        "category": "Bakery",
        "photo_display": None,
        "ingredients_display": "Flour, butter",
        "allergens_display": "Gluten, milk",
        "original_price": "10.00",
        "discounted_price": "4.50",
        "free_quantity": 3,
        "pickup_address": "12 Bliss Street, Beirut",
        # 14:00 UTC is 16:00 in Beirut in November (UTC+2).
        "pickup_start": "2026-11-05T14:00:00Z",
        "pickup_end": "2026-11-05T16:00:00Z",
        "shop": {
            "name": "Cedar Bakery",
            "area": "Hamra",
            "public_contact": "+961 1 234 567",
        },
    }
    data.update(overrides)
    return data


class HomePageLinkTests(TestCase):

    def test_home_page_links_to_browsing(self):
        response = self.client.get(reverse("home"))

        self.assertContains(response, f'href="{reverse("browse-offers")}"')


class BrowseOffersPageTests(TestCase):

    def setUp(self):
        self.url = reverse("browse-offers")

    def browse(self, *, return_value=None, side_effect=None):
        with patch_service(
            "browse_listings",
            return_value=return_value,
            side_effect=side_effect,
        ) as browse_api:
            response = self.client.get(self.url)
        return response, browse_api

    def test_offers_are_shown_as_cards_without_signing_in(self):
        response, browse_api = self.browse(return_value=(200, [offer()]))

        self.assertEqual(response.status_code, 200)
        browse_api.assert_called_once_with()  # no token is sent
        self.assertContains(response, "Croissant Box")
        self.assertContains(response, "Cedar Bakery")
        self.assertContains(response, "Hamra")
        self.assertContains(response, "$4.50")
        self.assertContains(response, "$10.00")
        self.assertContains(response, "3 left")
        self.assertContains(response, "Gluten, milk")
        self.assertContains(response, f'href="{reverse("offer-detail", args=[42])}"')

    def test_times_are_shown_in_lebanon_time(self):
        response, _ = self.browse(return_value=(200, [offer()]))

        self.assertContains(response, "Thu 5 Nov, 16:00–18:00")

    def test_summer_times_use_lebanon_daylight_saving(self):
        response, _ = self.browse(
            return_value=(
                200,
                [offer(
                    pickup_start="2026-07-01T14:00:00+00:00",
                    pickup_end="2026-07-01T15:30:00+00:00",
                )],
            )
        )

        self.assertContains(response, "Wed 1 Jul, 17:00–18:30")

    def test_window_crossing_midnight_shows_both_days(self):
        response, _ = self.browse(
            return_value=(
                200,
                [offer(
                    pickup_start="2026-11-05T21:00:00+02:00",
                    pickup_end="2026-11-06T01:00:00+02:00",
                )],
            )
        )

        self.assertContains(response, "Thu 5 Nov, 21:00 – Fri 6 Nov, 01:00")

    def test_missing_allergens_show_unknown(self):
        response, _ = self.browse(
            return_value=(200, [offer(allergens_display="")])
        )

        self.assertContains(response, "<dd>Unknown</dd>", html=True)

    def test_no_offers_shows_a_message(self):
        response, _ = self.browse(return_value=(200, []))

        self.assertContains(response, "no food available right now")

    def test_marketplace_unreachable_shows_friendly_message(self):
        response, _ = self.browse(side_effect=ServiceUnavailable("marketplace"))

        self.assertContains(response, UNAVAILABLE, status_code=503)

    def test_unexpected_marketplace_answer_shows_friendly_message(self):
        for answer in ((400, {"detail": "bad"}), (200, {"not": "a list"})):
            with self.subTest(answer=answer):
                response, _ = self.browse(return_value=answer)

                self.assertContains(response, UNAVAILABLE, status_code=502)

    def test_only_expected_fields_reach_the_page(self):
        leaky = offer(
            operator_user_id=987654,
            shop={
                "name": "Cedar Bakery",
                "area": "Hamra",
                "public_contact": "+961 1 234 567",
                "email": "owner@example.test",
            },
        )

        response, _ = self.browse(return_value=(200, [leaky]))

        self.assertNotContains(response, "owner@example.test")
        self.assertNotContains(response, "987654")

    def test_external_photo_links_are_not_rendered(self):
        response, _ = self.browse(
            return_value=(
                200,
                [offer(photo_display="https://evil.example/photo.jpg")],
            )
        )

        self.assertNotContains(response, "evil.example")
        self.assertContains(response, "No photo")

    @override_settings(
        DEBUG=True,
        SERVICE_URLS={**settings.SERVICE_URLS, "marketplace": "http://127.0.0.1:8002"},
    )
    def test_marketplace_photo_is_shown(self):
        response, _ = self.browse(
            return_value=(
                200,
                [offer(photo_display="/media/listing_photos/42/photo.jpg")],
            )
        )

        self.assertContains(
            response, "http://127.0.0.1:8002/media/listing_photos/42/photo.jpg"
        )

    @override_settings(
        DEBUG=True,
        SERVICE_URLS={**settings.SERVICE_URLS, "marketplace": "http://marketplace:8000"},
        MARKETPLACE_PUBLIC_URL="http://127.0.0.1:8002",
    )
    def test_photo_uses_browser_address_under_docker(self):
        response, _ = self.browse(
            return_value=(
                200,
                [offer(photo_display="/media/listing_photos/42/photo.jpg")],
            )
        )

        self.assertContains(
            response, "http://127.0.0.1:8002/media/listing_photos/42/photo.jpg"
        )
        self.assertNotContains(response, "marketplace:8000")

    def test_post_is_not_allowed(self):
        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 405)


class OfferDetailPageTests(TestCase):

    def setUp(self):
        self.url = reverse("offer-detail", args=[42])

    def view(self, *, return_value=None, side_effect=None):
        with patch_service(
            "listing_offer",
            return_value=return_value,
            side_effect=side_effect,
        ) as detail_api:
            response = self.client.get(self.url)
        return response, detail_api

    def test_offer_details_are_shown(self):
        response, detail_api = self.view(
            return_value=(200, offer(terms_revision=2))
        )

        self.assertEqual(response.status_code, 200)
        detail_api.assert_called_once_with(42)
        self.assertContains(response, "Croissant Box")
        self.assertContains(response, "Fresh pastries")
        self.assertContains(response, "Thu 5 Nov, 16:00–18:00")
        self.assertContains(response, "12 Bliss Street, Beirut")
        self.assertContains(response, "Flour, butter")
        self.assertContains(response, "Gluten, milk")
        self.assertContains(response, "Cedar Bakery")
        self.assertContains(response, "Hamra")
        self.assertContains(response, 'href="tel:+9611234567"')

    def test_missing_ingredients_and_allergens_show_unknown(self):
        response, _ = self.view(
            return_value=(
                200,
                offer(ingredients_display=None, allergens_display="  "),
            )
        )

        content = response.content.decode()
        self.assertEqual(content.count("<dd>Unknown</dd>"), 2)

    def test_hidden_offer_shows_not_available_page(self):
        response, _ = self.view(
            return_value=(
                404,
                {"code": "not_found", "detail": "This offer is not available."},
            )
        )

        self.assertContains(response, "This offer isn't available", status_code=404)
        self.assertContains(response, reverse("browse-offers"), status_code=404)

    def test_marketplace_unreachable_shows_friendly_message(self):
        response, _ = self.view(side_effect=ServiceUnavailable("marketplace"))

        self.assertContains(response, UNAVAILABLE, status_code=503)

    def test_unexpected_marketplace_answer_shows_friendly_message(self):
        response, _ = self.view(return_value=(400, {}))

        self.assertContains(response, UNAVAILABLE, status_code=502)

    def test_operator_identity_is_never_shown(self):
        response, _ = self.view(
            return_value=(
                200,
                offer(operator_user_id=987654, email="owner@example.test"),
            )
        )

        self.assertNotContains(response, "owner@example.test")
        self.assertNotContains(response, "987654")
